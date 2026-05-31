#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from competition import load_competition

ACT_RE = re.compile(r"/activities/(\d+)")


def extract_first_time_date(gpx_path: Path) -> str | None:
    try:
        root = ET.parse(gpx_path).getroot()
    except Exception:
        return None
    for el in root.iter():
        if el.tag.endswith("time") and (el.text or "").strip():
            txt = (el.text or "").strip().replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(txt).date().isoformat()
            except Exception:
                continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate stage_links consistency (duplicate activity IDs + GPX date checks).")
    ap.add_argument("--competition-dir", required=True)
    args = ap.parse_args()

    comp = load_competition(args.competition_dir)
    base = comp.root
    stages_payload = json.loads(comp.stages_json.read_text(encoding="utf-8"))
    stage_date = {s["stage_id"]: s["date"] for s in stages_payload.get("stages", [])}

    by_activity: dict[str, list[tuple[str, str]]] = defaultdict(list)
    dup_count = 0
    gpx_missing = 0
    gpx_date_mismatch = 0

    for sp in sorted((base / "stage_links").glob("S*.json")):
        sid = sp.stem
        payload = json.loads(sp.read_text(encoding="utf-8"))
        expected_date = stage_date.get(sid)
        for row in payload.get("activities", []):
            rid = str(row.get("rider_id", "")).strip()
            url = str(row.get("activity_url") or "").strip()
            if not rid or not url:
                continue
            m = ACT_RE.search(url)
            if not m:
                continue
            aid = m.group(1)
            by_activity[aid].append((sid, rid))

            gpx = base / "courses" / sid / f"{rid}__activity_{aid}.gpx"
            if not gpx.exists() or gpx.stat().st_size == 0:
                gpx_missing += 1
                continue
            d = extract_first_time_date(gpx)
            if d and expected_date and d != expected_date:
                gpx_date_mismatch += 1
                print(f"[GPX_DATE_MISMATCH] {sid} {rid} {aid} gpx_date={d} stage_date={expected_date}")

    for aid, occ in sorted(by_activity.items()):
        stages = sorted({s for s, _ in occ})
        if len(stages) > 1:
            dup_count += 1
            where = ", ".join(f"{s}:{r}" for s, r in occ)
            print(f"[DUP_ACTIVITY_ID] {aid} -> {where}")

    print("=== Validation Summary ===")
    print(f"activity_ids_with_multiple_stages: {dup_count}")
    print(f"missing_gpx_for_activity_rows: {gpx_missing}")
    print(f"gpx_date_mismatch: {gpx_date_mismatch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
