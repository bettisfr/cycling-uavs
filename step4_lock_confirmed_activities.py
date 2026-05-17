#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 4: lock all currently assigned activity rows in stage_links.")
    ap.add_argument("--dataset-dir", default="giro_2026")
    args = ap.parse_args()

    base = Path(args.dataset_dir)
    links = base / "stage_links"

    files_updated = 0
    rows_locked = 0
    rows_already_locked = 0

    for p in sorted(links.glob("S*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        changed = False
        for row in d.get("activities", []):
            if not row.get("activity_url"):
                continue
            if bool(row.get("locked")):
                rows_already_locked += 1
                continue
            row["locked"] = True
            rows_locked += 1
            changed = True
        if changed:
            p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            files_updated += 1

    print("=== Step4 Summary ===")
    print(f"files_updated: {files_updated}")
    print(f"rows_locked_now: {rows_locked}")
    print(f"rows_already_locked: {rows_already_locked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
