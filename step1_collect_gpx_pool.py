#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from import_rider_raw import extract_pairs

MONTH_HASH = "interval_type?chart_type=miles&interval_type=month&interval=202605&year_offset=0"


def with_month_hash(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, MONTH_HASH))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Step 1 pipeline: fetch rider raw pages, parse activities, download GPX to pool."
    )
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--from-date", default="2026-05-08", help="Inclusive YYYY-MM-DD")
    ap.add_argument("--timeout-sec", type=int, default=20)
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--scroll-steps", type=int, default=12)
    ap.add_argument("--scroll-wait-ms", type=int, default=1200)
    ap.add_argument("--sleep", type=float, default=2.0)
    ap.add_argument("--local-tz", default="Europe/Rome")
    ap.add_argument("--from-rider-id", default=None)
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent
    dataset_dir = Path(args.dataset_dir)
    cookie_file = repo / "strava_session_cookie.txt"
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie:
        raise SystemExit("Empty strava_session_cookie.txt")

    from_date = date.fromisoformat(args.from_date)

    riders = json.loads((dataset_dir / "riders.json").read_text(encoding="utf-8")).get("riders", [])
    riders = sorted([r for r in riders if isinstance(r, dict)], key=lambda r: r.get("rider_id", ""))

    fetch_script = repo / "fetch_rider_raw_brave.py"
    gpx_script = repo / "lib" / "strava_to_gpx.py"
    gpx_store = dataset_dir / "gpx_store"
    gpx_store.mkdir(parents=True, exist_ok=True)

    total_riders = 0
    total_pairs = 0
    total_new = 0
    total_skip_existing = 0
    total_fail = 0

    for r in riders:
        rid = str(r.get("rider_id", "")).strip()
        if not rid:
            continue
        if args.from_rider_id and rid < args.from_rider_id:
            continue
        if r.get("enabled", True) is False:
            print(f"SKIP {rid} disabled")
            continue
        url = str(r.get("strava_athlete_url") or "").strip()
        if not url:
            print(f"SKIP {rid} no_strava_url")
            continue

        total_riders += 1

        cmd_fetch = [
            "/home/fra/pyvenv/bin/python",
            str(fetch_script),
            "--url",
            with_month_hash(url),
            "--rider-id",
            rid,
            "--dataset-dir",
            str(dataset_dir),
            "--session-cookie-file",
            str(cookie_file),
            "--timeout-sec",
            str(args.timeout_sec),
            "--scroll-steps",
            str(args.scroll_steps),
            "--scroll-wait-ms",
            str(args.scroll_wait_ms),
        ]
        if args.headless:
            cmd_fetch.append("--headless")

        rf = subprocess.run(cmd_fetch, capture_output=True, text=True)
        if rf.returncode != 0:
            err = ((rf.stderr or rf.stdout) or "").strip().replace("\n", " | ")
            print(f"FAIL {rid} fetch {err}")
            total_fail += 1
            continue

        raw_path = dataset_dir / "raw" / "riders" / f"{rid}.txt"
        if not raw_path.exists():
            print(f"FAIL {rid} raw_missing {raw_path}")
            total_fail += 1
            continue

        html = raw_path.read_text(encoding="utf-8", errors="ignore")

        expected_owner_paths: set[str] = set()
        m = re.search(r"strava\.com/(pros|athletes)/([^/?#]+)", url)
        if m:
            expected_owner_paths.add(f"/{m.group(1)}/{m.group(2)}")
            if m.group(1) == "pros":
                expected_owner_paths.add(f"/athletes/{m.group(2)}")

        pairs = extract_pairs(html, today=date.today(), expected_owner_paths=expected_owner_paths or None)
        pairs = [p for p in pairs if date.fromisoformat(str(p["date"])) >= from_date]
        pairs = sorted(pairs, key=lambda p: str(p["date"]))
        total_pairs += len(pairs)

        if pairs:
            dmin = str(pairs[0]["date"])
            dmax = str(pairs[-1]["date"])
        else:
            dmin = "-"
            dmax = "-"

        # Keep at most one activity per rider+activity_id
        seen_ids: set[str] = set()
        rider_new = 0
        rider_existing = 0
        rider_fail = 0

        for p in pairs:
            aid = str(p["activity_id"])
            if aid in seen_ids:
                continue
            seen_ids.add(aid)

            out = gpx_store / f"{rid}__activity_{aid}.gpx"
            if out.exists() and out.stat().st_size > 0:
                rider_existing += 1
                continue

            cmd_gpx = [
                "/home/fra/pyvenv/bin/python",
                str(gpx_script),
                "--session-cookie",
                cookie,
                "--local-tz",
                args.local_tz,
                f"https://www.strava.com/activities/{aid}",
                "-o",
                str(out),
            ]
            rg = subprocess.run(cmd_gpx, capture_output=True, text=True)
            if rg.returncode == 0 and out.exists() and out.stat().st_size > 0:
                rider_new += 1
                total_new += 1
            else:
                rider_fail += 1
                total_fail += 1
            time.sleep(args.sleep)

        total_skip_existing += rider_existing
        print(
            f"OK   {rid} | activities={len(seen_ids)} | dates={dmin}..{dmax} | "
            f"new={rider_new} existing={rider_existing} fail={rider_fail}"
        )

    print("\n=== Step1 Summary ===")
    print(f"riders_processed: {total_riders}")
    print(f"activities_parsed_from_date: {total_pairs}")
    print(f"gpx_new_downloaded: {total_new}")
    print(f"gpx_already_in_pool: {total_skip_existing}")
    print(f"failures: {total_fail}")
    print(f"pool_dir: {gpx_store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
