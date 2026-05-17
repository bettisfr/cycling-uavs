#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ACT_RE = re.compile(r"/activities/(\d+)")


def first_local_date_from_gpx(gpx_path: Path, tz_name: str) -> str | None:
    try:
        root = ET.parse(gpx_path).getroot()
    except Exception:
        return None
    tz = ZoneInfo(tz_name)
    for el in root.iter():
        if not el.tag.endswith("time"):
            continue
        txt = (el.text or "").strip()
        if not txt:
            continue
        iso = txt.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except Exception:
            continue
        if dt.tzinfo is None:
            continue
        return dt.astimezone(tz).date().isoformat()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Reassign stage_links rows using GPX date (local timezone).")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--local-tz", default="Europe/Rome")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base = Path(args.dataset_dir)
    links_dir = base / "stage_links"
    store = base / "gpx_store"

    stages_payload = json.loads((base / "stages.json").read_text(encoding="utf-8"))
    stages = stages_payload.get("stages", [])
    stage_by_date = {s["date"]: s["stage_id"] for s in stages if isinstance(s, dict) and s.get("date") and s.get("stage_id")}
    known_stage_ids = sorted(stage_by_date.values(), key=lambda s: int(s[1:]))

    # Load all stage files and index rows by rider_id.
    stage_data: dict[str, dict] = {}
    stage_rows: dict[str, dict[str, dict]] = {}
    for sid in known_stage_ids:
        p = links_dir / f"{sid}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        stage_data[sid] = d
        stage_rows[sid] = {str(r.get("rider_id")): r for r in d.get("activities", []) if isinstance(r, dict)}

    # Build candidates from current activity rows in stage_links.
    by_rider: dict[str, list[tuple[str, str]]] = defaultdict(list)  # rider -> [(stage_id, aid)]
    for sid, d in stage_data.items():
        for row in d.get("activities", []):
            rid = str(row.get("rider_id", "")).strip()
            url = str(row.get("activity_url") or "").strip()
            if not rid or not url:
                continue
            m = ACT_RE.search(url)
            if not m:
                continue
            by_rider[rid].append((sid, m.group(1)))

    moved = 0
    cleared = 0
    kept = 0
    missing_gpx = 0
    unresolved_date = 0

    # For each rider+aid present somewhere, decide destination stage by GPX date.
    seen_rider_aid: set[tuple[str, str]] = set()
    for rid, pairs in by_rider.items():
        for _, aid in pairs:
            key = (rid, aid)
            if key in seen_rider_aid:
                continue
            seen_rider_aid.add(key)

            gpx = store / f"{rid}__activity_{aid}.gpx"
            if not gpx.exists() or gpx.stat().st_size == 0:
                missing_gpx += 1
                continue
            gpx_date = first_local_date_from_gpx(gpx, args.local_tz)
            if not gpx_date:
                unresolved_date += 1
                continue
            target = stage_by_date.get(gpx_date)
            if not target:
                unresolved_date += 1
                continue

            target_row = stage_rows.get(target, {}).get(rid)
            if target_row is None:
                unresolved_date += 1
                continue

            wanted_url = f"https://www.strava.com/activities/{aid}"
            # Set target row
            if target_row.get("activity_url") != wanted_url:
                target_row["activity_url"] = wanted_url
                target_row["status"] = "found_public"
                moved += 1
            else:
                kept += 1

            # Clear same rider+aid from all other stages
            for sid, rows in stage_rows.items():
                if sid == target:
                    continue
                row = rows.get(rid)
                if row is None:
                    continue
                url = str(row.get("activity_url") or "")
                if url.endswith(f"/{aid}"):
                    row["activity_url"] = None
                    row["status"] = "not_checked"
                    row["locked"] = False
                    for k in ("gpx_start_hhmm", "gpx_km", "gpx_path", "gpx_file"):
                        row.pop(k, None)
                    cleared += 1

    if not args.dry_run:
        for sid, d in stage_data.items():
            p = links_dir / f"{sid}.json"
            p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"moved_to_gpx_date_stage={moved}")
    print(f"kept_already_correct={kept}")
    print(f"cleared_from_wrong_stage={cleared}")
    print(f"missing_gpx={missing_gpx}")
    print(f"unresolved_date={unresolved_date}")
    print(f"dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
