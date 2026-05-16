#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch rendered raw rider pages for all enabled riders.")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--session-cookie-file", default="strava_session_cookie.txt")
    ap.add_argument("--timeout-sec", type=int, default=10)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--from-rider-id", default=None, help="Start from rider id (inclusive), e.g. B026")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent
    dataset_dir = Path(args.dataset_dir)
    riders = json.loads((dataset_dir / "riders.json").read_text(encoding="utf-8"))["riders"]
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
            str(url),
            "--rider-id",
            rid,
            "--dataset-dir",
            str(dataset_dir),
            "--session-cookie-file",
            str(args.session_cookie_file),
            "--timeout-sec",
            str(args.timeout_sec),
        ]
        if args.headless:
            cmd.append("--headless")

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            ok += 1
            msg = (proc.stdout or "").strip().replace("\n", " | ")
            print(f"OK   {rid} {msg}")
        else:
            fail += 1
            err = ((proc.stderr or proc.stdout) or "").strip().replace("\n", " | ")
            print(f"FAIL {rid} {err}")

    print(f"SUMMARY ok={ok} fail={fail} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

