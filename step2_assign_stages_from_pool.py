#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

GPX_RE = re.compile(r"^(B\d{3})__activity_(\d+)\.gpx$")


@dataclass
class GpxInfo:
    rider_id: str
    activity_id: str
    gpx_path: Path
    local_date: str
    start_local_date: str
    end_local_date: str
    mid_local_date: str
    start_hhmm: str
    distance_km: float


def parse_gpx_info(path: Path, tz_name: str, date_mode: str = "mid") -> GpxInfo | None:
    m = GPX_RE.match(path.name)
    if not m:
        return None
    rider_id, activity_id = m.group(1), m.group(2)

    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None

    tz = ZoneInfo(tz_name)
    first_dt_local = None
    all_times_utc: list[datetime] = []
    prev = None
    dist_m = 0.0

    for el in root.iter():
        if not el.tag.endswith("trkpt"):
            continue
        lat = el.attrib.get("lat")
        lon = el.attrib.get("lon")
        if lat is None or lon is None:
            continue
        try:
            p = (float(lat), float(lon))
        except Exception:
            continue

        if prev is not None:
            # equirectangular approx is enough here for tie-breaking distances
            import math

            lat1, lon1 = prev
            lat2, lon2 = p
            r = 6371000.0
            x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2.0))
            y = math.radians(lat2 - lat1)
            dist_m += (x * x + y * y) ** 0.5 * r
        prev = p

        if first_dt_local is None:
            t = None
            for child in el:
                if child.tag.endswith("time") and (child.text or "").strip():
                    t = (child.text or "").strip()
                    break
            if t:
                try:
                    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                    if dt.tzinfo is not None:
                        first_dt_local = dt.astimezone(tz)
                        all_times_utc.append(dt)
                except Exception:
                    pass
            continue
        # Collect times for non-first points too
        t = None
        for child in el:
            if child.tag.endswith("time") and (child.text or "").strip():
                t = (child.text or "").strip()
                break
        if t:
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    all_times_utc.append(dt)
            except Exception:
                pass

    if first_dt_local is None or not all_times_utc:
        return None

    t0 = min(all_times_utc)
    t1 = max(all_times_utc)
    tm = t0 + (t1 - t0) / 2
    start_local = t0.astimezone(tz)
    end_local = t1.astimezone(tz)
    mid_local = tm.astimezone(tz)

    if date_mode == "start":
        chosen_local_date = start_local.date().isoformat()
    elif date_mode == "end":
        chosen_local_date = end_local.date().isoformat()
    else:
        chosen_local_date = mid_local.date().isoformat()

    return GpxInfo(
        rider_id=rider_id,
        activity_id=activity_id,
        gpx_path=path,
        local_date=chosen_local_date,
        start_local_date=start_local.date().isoformat(),
        end_local_date=end_local.date().isoformat(),
        mid_local_date=mid_local.date().isoformat(),
        start_hhmm=first_dt_local.strftime("%H:%M"),
        distance_km=dist_m / 1000.0,
    )


def stage_num(stage_id: str) -> int:
    try:
        return int(stage_id.lstrip("S"))
    except Exception:
        return -1


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 2: assign stages from GPX pool by local GPX date.")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--local-tz", default="Europe/Rome")
    ap.add_argument("--rider-id", default=None, help="Optional single rider (e.g. B002)")
    ap.add_argument("--lock", action="store_true", help="Set locked=true for assigned rows")
    ap.add_argument(
        "--date-mode",
        choices=["mid", "start", "end"],
        default="mid",
        help="Which GPX timestamp day to use for stage assignment (default: mid).",
    )
    args = ap.parse_args()
    # Stage-specific start-time eligibility rules (local HH:MM, inclusive).
    # S10 is an ITT day; morning reconnaissance should be ignored.
    min_start_by_stage = {
        "S10": "13:00",
    }

    base = Path(args.dataset_dir)
    riders_payload = json.loads((base / "riders.json").read_text(encoding="utf-8"))
    riders = [r for r in riders_payload.get("riders", []) if isinstance(r, dict)]

    stages_payload = json.loads((base / "stages.json").read_text(encoding="utf-8"))
    stages = [s for s in stages_payload.get("stages", []) if isinstance(s, dict)]
    date_to_stage = {s["date"]: s["stage_id"] for s in stages if s.get("date") and s.get("stage_id")}
    stage_ids = sorted({s["stage_id"] for s in stages if s.get("stage_id")}, key=stage_num)

    # Load stage jsons once
    stage_json: dict[str, dict] = {}
    stage_rows: dict[str, dict[str, dict]] = {}
    for sid in stage_ids:
        p = base / "stage_links" / f"{sid}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        stage_json[sid] = d
        stage_rows[sid] = {str(r.get("rider_id")): r for r in d.get("activities", []) if isinstance(r, dict)}

    gpx_store = base / "gpx_store"
    courses = base / "courses"

    riders_done = 0
    assigned_total = 0

    for r in riders:
        rid = str(r.get("rider_id", "")).strip()
        if not rid:
            continue
        if args.rider_id and rid != args.rider_id:
            continue
        if r.get("enabled", True) is False:
            continue

        ws = int(r.get("withdraw_stage", -1)) if str(r.get("withdraw_stage", "-1")).lstrip("-").isdigit() else -1

        # Parse all GPX for rider from pool.
        infos: list[GpxInfo] = []
        for p in sorted(gpx_store.glob(f"{rid}__activity_*.gpx")):
            info = parse_gpx_info(p, args.local_tz, date_mode=args.date_mode)
            if info is not None:
                infos.append(info)

        # Choose one per stage by max distance.
        chosen_by_stage: dict[str, GpxInfo] = {}
        ignored_nonstage = 0
        ignored_withdraw = 0
        for info in infos:
            sid = date_to_stage.get(info.local_date)
            if not sid:
                ignored_nonstage += 1
                continue
            min_hhmm = min_start_by_stage.get(sid)
            if min_hhmm and info.start_hhmm < min_hhmm:
                ignored_nonstage += 1
                continue
            if ws >= 0 and stage_num(sid) > ws:
                ignored_withdraw += 1
                continue
            prev = chosen_by_stage.get(sid)
            if prev is None or info.distance_km > prev.distance_km:
                chosen_by_stage[sid] = info

        # Clear rider rows across stages + remove rider symlinks.
        for sid in stage_ids:
            row = stage_rows.get(sid, {}).get(rid)
            if row is None:
                continue
            if bool(row.get("locked")):
                # Never modify locked rows in step2.
                continue
            row["activity_url"] = None
            row["status"] = "not_checked"
            row["locked"] = False
            for k in ("gpx_start_hhmm", "gpx_km", "gpx_path", "gpx_file"):
                row.pop(k, None)

            stage_dir = courses / sid
            stage_dir.mkdir(parents=True, exist_ok=True)
            for old in stage_dir.glob(f"{rid}__activity_*.gpx"):
                old.unlink(missing_ok=True)

        # Assign chosen rows and symlinks.
        assigned = 0
        for sid, info in sorted(chosen_by_stage.items(), key=lambda x: stage_num(x[0])):
            row = stage_rows.get(sid, {}).get(rid)
            if row is None:
                continue
            if bool(row.get("locked")):
                # Keep locked assignment untouched.
                continue
            row["activity_url"] = f"https://www.strava.com/activities/{info.activity_id}"
            row["status"] = "found_public"
            if args.lock:
                row["locked"] = True
            row["gpx_start_hhmm"] = info.start_hhmm
            row["gpx_km"] = f"{info.distance_km:.1f}"
            row["gpx_path"] = str(courses / sid / info.gpx_path.name)

            dst = courses / sid / info.gpx_path.name
            if dst.exists() or dst.is_symlink():
                dst.unlink(missing_ok=True)
            try:
                dst.symlink_to(Path("..") / ".." / "gpx_store" / info.gpx_path.name)
            except Exception:
                shutil.copy2(info.gpx_path, dst)

            assigned += 1

        riders_done += 1
        assigned_total += assigned
        rng = (
            f"{min(i.local_date for i in infos)}..{max(i.local_date for i in infos)}"
            if infos
            else "-"
        )
        print(
            f"OK   {rid} | pool={len(infos)} | dates={rng} | assigned={assigned} "
            f"ignored_nonstage={ignored_nonstage} ignored_withdraw={ignored_withdraw}"
        )

    # Persist stage json updates.
    for sid, d in stage_json.items():
        p = base / "stage_links" / f"{sid}.json"
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n=== Step2 Summary ===")
    print(f"riders_processed: {riders_done}")
    print(f"total_assigned_rows: {assigned_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
    # Stage-specific start-time eligibility rules (local time HH:MM, inclusive).
    min_start_by_stage = {
        "S10": "13:00",
    }
