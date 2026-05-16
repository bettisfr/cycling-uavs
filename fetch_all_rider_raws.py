#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from import_rider_raw import extract_pairs

MONTH_HASH = "interval_type?chart_type=miles&interval_type=month&interval=202605&year_offset=0"


def with_month_hash(url: str) -> str:
    parts = urlsplit(url)
    # Keep base URL and force the monthly feed hash view.
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, MONTH_HASH))


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch rendered raw rider pages for all enabled riders.")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--timeout-sec", type=int, default=10)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--scroll-steps", type=int, default=0, help="Forwarded to fetch_rider_raw_brave.py")
    ap.add_argument("--scroll-wait-ms", type=int, default=1200, help="Forwarded to fetch_rider_raw_brave.py")
    ap.add_argument("--from-rider-id", default=None, help="Start from rider id (inclusive), e.g. B026")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent
    cookie_file = repo / "strava_session_cookie.txt"
    dataset_dir = Path(args.dataset_dir)
    riders = json.loads((dataset_dir / "riders.json").read_text(encoding="utf-8"))["riders"]
    stages = json.loads((dataset_dir / "stages.json").read_text(encoding="utf-8"))["stages"]
    by_date = {s.get("date"): s.get("stage_id") for s in stages if isinstance(s, dict)}
    fetch_script = repo / "fetch_rider_raw_brave.py"

    ok = 0
    fail = 0
    skipped = 0

    riders_sorted = sorted(riders, key=lambda r: r.get("rider_id", ""))
    for r in riders_sorted:
        rid = str(r.get("rider_id", ""))
        if args.from_rider_id and rid < args.from_rider_id:
            continue
        url = r.get("strava_athlete_url")
        if not url or r.get("enabled", True) is False:
            skipped += 1
            print(f"SKIP {rid} (no_url_or_disabled)")
            continue

        cmd = [
            "/home/fra/pyvenv/bin/python",
            str(fetch_script),
            "--url",
            with_month_hash(str(url)),
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
            cmd.append("--headless")

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            ok += 1
            out_text = (proc.stdout or "")
            m_bytes = re.search(r"^bytes=(\d+)\s*$", out_text, flags=re.MULTILINE)
            msg = f"bytes={m_bytes.group(1)}" if m_bytes else "bytes=?"
            raw_path = dataset_dir / "raw" / "riders" / f"{rid}.txt"
            stages_found = []
            if raw_path.exists():
                html = raw_path.read_text(encoding="utf-8", errors="ignore")
                expected_owner_paths: set[str] = set()
                m = re.search(r"strava\.com/(pros|athletes)/([^/?#]+)", str(url))
                if m:
                    expected_owner_paths.add(f"/{m.group(1)}/{m.group(2)}")
                    if m.group(1) == "pros":
                        expected_owner_paths.add(f"/athletes/{m.group(2)}")
                pairs = extract_pairs(html, today=date.today(), expected_owner_paths=expected_owner_paths or None)
                stage_km: dict[str, float] = {}
                for p in pairs:
                    sid = by_date.get(str(p.get("date")))
                    if isinstance(sid, str) and sid.startswith("S"):
                        dist = p.get("distance_km")
                        if isinstance(dist, (int, float)):
                            prev = stage_km.get(sid)
                            val = float(dist)
                            if prev is None or val > prev:
                                stage_km[sid] = val
                        elif sid not in stage_km:
                            stage_km[sid] = -1.0
                for sid in sorted(stage_km.keys(), key=lambda x: int(x[1:])):
                    km = stage_km[sid]
                    if km >= 0:
                        stages_found.append(f"{sid} ({km:.2f})")
                    else:
                        stages_found.append(f"{sid}")
            stage_msg = ", ".join(stages_found) if stages_found else "-"
            print(f"OK   {rid} {msg} | stages={stage_msg}")
        else:
            fail += 1
            err = ((proc.stderr or proc.stdout) or "").strip().replace("\n", " | ")
            print(f"FAIL {rid} {err}")

    print(f"SUMMARY ok={ok} fail={fail} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
