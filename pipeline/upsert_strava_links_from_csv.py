#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Upsert strava_athlete_url in riders.json from CSV.")
    ap.add_argument("--competition-dir", required=True, help="Competition directory (contains riders.json).")
    ap.add_argument("--csv-file", required=True, help="CSV file with columns: bib,strava_athlete_url.")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    comp = Path(args.competition_dir).resolve()
    csv_file = Path(args.csv_file).resolve()
    riders_path = comp / "riders.json"

    payload = json.loads(riders_path.read_text(encoding="utf-8"))
    riders = payload.get("riders", [])
    by_bib = {int(r["bib"]): r for r in riders if isinstance(r, dict) and str(r.get("bib", "")).isdigit()}

    updated = 0
    with csv_file.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            bib_raw = str(row.get("bib", "")).strip()
            if not bib_raw.isdigit():
                continue
            bib = int(bib_raw)
            r = by_bib.get(bib)
            if r is None:
                continue
            url = str(row.get("strava_athlete_url", "")).strip()
            new_val = url or None
            if r.get("strava_athlete_url") != new_val:
                r["strava_athlete_url"] = new_val
                updated += 1

    payload["version"] = int(payload.get("version", 1)) + 1
    riders_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = len(riders)
    with_url = sum(1 for r in riders if r.get("strava_athlete_url"))
    missing = total - with_url
    print(f"[OK] updated={updated} total={total} with_url={with_url} missing={missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
