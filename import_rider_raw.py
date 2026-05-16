#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

from bs4 import BeautifulSoup


DATE_RE = re.compile(r"\bMay\s+(\d{1,2}),\s+2026\b")
ACT_RE = re.compile(r"^/activities/(\d+)$")
DIST_RE = re.compile(r"\bDistance\s+([0-9]+(?:\.[0-9]+)?)\s*km\b", re.IGNORECASE)


def parse_date_label(label: str, today: date) -> str | None:
    label = label.strip()
    if label == "Yesterday":
        return (today - timedelta(days=1)).isoformat()
    m = DATE_RE.match(label)
    if m:
        return f"2026-05-{int(m.group(1)):02d}"
    return None


def extract_pairs(html: str, today: date) -> list[dict[str, str | float | None]]:
    soup = BeautifulSoup(html, "html.parser")
    entries = soup.select(".CQdSY")
    if not entries:
        return []

    text = soup.get_text("\n", strip=True)
    labels = re.findall(r"\b(?:Yesterday|May\s+\d{1,2},\s+2026)\b", text)
    labels = labels[: len(entries)]

    out: list[dict[str, str | float | None]] = []
    for i, entry in enumerate(entries):
        aid = None
        h3 = entry.find("h3")
        if h3:
            for a in h3.find_all("a", href=True):
                m = ACT_RE.fullmatch(a["href"])
                if m:
                    aid = m.group(1)
                    break
        if not aid:
            continue
        if i >= len(labels):
            continue
        d = parse_date_label(labels[i], today)
        if not d:
            continue
        txt = " ".join(entry.get_text(" ", strip=True).split())
        m_dist = DIST_RE.search(txt)
        dist_km = float(m_dist.group(1)) if m_dist else None
        out.append({"date": d, "activity_id": aid, "distance_km": dist_km})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Import rider profile raw HTML and map activities to stages by date.")
    ap.add_argument("--rider-id", default=None, help="e.g. B002")
    ap.add_argument("--all", action="store_true", help="Process all raw rider files in dataset raw/riders/")
    ap.add_argument("--html-file", default=None, help="Raw rider page HTML path")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--year", type=int, default=2026)
    args = ap.parse_args()

    base = Path(args.dataset_dir)
    stages = json.loads((base / "stages.json").read_text(encoding="utf-8"))["stages"]
    stage_by_date = {s["date"]: s["stage_id"] for s in stages}
    stage_dates = set(stage_by_date.keys())
    stage_meta_by_id = {s["stage_id"]: s for s in stages}
    overrides_path = base / "stage_links" / "manual_overrides.json"
    overrides = {}
    if overrides_path.exists():
        overrides = json.loads(overrides_path.read_text(encoding="utf-8"))

    if args.all:
        raw_dir = base / "raw" / "riders"
        tasks = []
        for p in sorted(raw_dir.glob("B*.txt")):
            tasks.append((p.stem, p))
        for p in sorted(raw_dir.glob("B*.html")):
            if (p.stem, p.with_suffix(".txt")) not in tasks:
                tasks.append((p.stem, p))
        if not tasks:
            raise SystemExit(f"No rider raw files found in: {raw_dir}")
    else:
        if not args.rider_id:
            raise SystemExit("Provide --rider-id or use --all")
        if args.html_file:
            raw_path = Path(args.html_file)
        else:
            txt = base / "raw" / "riders" / f"{args.rider_id}.txt"
            htmlf = base / "raw" / "riders" / f"{args.rider_id}.html"
            raw_path = txt if txt.exists() else htmlf
        tasks = [(args.rider_id, raw_path)]

    total_updated = 0
    total_riders = 0
    total_pairs = 0
    total_selected = 0
    total_skipped_no_candidate = 0
    total_ignored_out = 0
    total_matched_dates = 0
    for rider_id, raw_path in tasks:
        if not raw_path.exists():
            print(f"[SKIP] {rider_id} missing raw: {raw_path}")
            continue
        total_riders += 1
        html = raw_path.read_text(encoding="utf-8", errors="ignore")
        pairs = extract_pairs(html, today=date(args.year, 5, 16))

        # Group candidates by day (some riders may have more than one activity/day).
        by_day: dict[str, list[dict[str, str | float | None]]] = {}
        ignored_out_of_stage_calendar = 0
        for row in pairs:
            d = str(row["date"])
            if d not in stage_dates:
                ignored_out_of_stage_calendar += 1
                continue
            by_day.setdefault(d, []).append(row)

        updated = 0
        matched_dates = 0
        selected = 0
        skipped_no_candidate = 0
        for d, candidates in by_day.items():
            sid = stage_by_date.get(d)
            if not sid:
                continue
            matched_dates += 1

            # Selection rule:
            # - consider only candidates with distance > 100 km
            # - among them, choose min abs(distance - stage.distance_km)
            stage_distance = stage_meta_by_id.get(sid, {}).get("distance_km")
            if not isinstance(stage_distance, (int, float)):
                stage_distance = None

            valid: list[dict[str, str | float | None]] = []
            for c in candidates:
                dist = c.get("distance_km")
                if isinstance(dist, (int, float)) and float(dist) > 100.0:
                    valid.append(c)
            if not valid:
                skipped_no_candidate += 1
                continue
            if stage_distance is not None:
                chosen = min(valid, key=lambda c: abs(float(c["distance_km"]) - float(stage_distance)))  # type: ignore[index]
            else:
                chosen = valid[0]
            selected += 1

            aid = str(chosen["activity_id"])
            p = base / "stage_links" / f"{sid}.json"
            if not p.exists():
                continue
            payload = json.loads(p.read_text(encoding="utf-8"))
            row = next((a for a in payload.get("activities", []) if a.get("rider_id") == rider_id), None)
            if not row:
                continue
            new_url = f"https://www.strava.com/activities/{aid}"
            if row.get("activity_url") != new_url:
                row["activity_url"] = new_url
                row["status"] = "found_public"
                p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                updated += 1

        # Apply manual override after auto-selection to keep known edge cases stable.
        # Format:
        # {
        #   "S04": { "B135": "https://www.strava.com/activities/18478655091" },
        #   "S05": { "B135": null }
        # }
        for sid, stage_overrides in overrides.items():
            if not isinstance(stage_overrides, dict):
                continue
            forced = stage_overrides.get(rider_id, "__NO__")
            if forced == "__NO__":
                continue
            p = base / "stage_links" / f"{sid}.json"
            if not p.exists():
                continue
            payload = json.loads(p.read_text(encoding="utf-8"))
            row = next((a for a in payload.get("activities", []) if a.get("rider_id") == rider_id), None)
            if not row:
                continue
            if forced is None:
                wanted_url = None
                wanted_status = "not_checked"
            else:
                wanted_url = str(forced)
                wanted_status = "found_public"
            if row.get("activity_url") != wanted_url or row.get("status") != wanted_status:
                row["activity_url"] = wanted_url
                row["status"] = wanted_status
                p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                updated += 1

        total_updated += updated
        total_pairs += len(pairs)
        total_selected += selected
        total_skipped_no_candidate += skipped_no_candidate
        total_ignored_out += ignored_out_of_stage_calendar
        total_matched_dates += matched_dates
        status = "UPDATED" if updated > 0 else "OK"
        print(
            f"[{status}] {rider_id} | "
            f"pairs={len(pairs)} "
            f"stage_days={matched_dates} "
            f"selected={selected} "
            f"updated={updated} "
            f"skip_lt100={skipped_no_candidate} "
            f"ignored_out={ignored_out_of_stage_calendar}"
        )

    if args.all:
        print()
        print("=== Import Summary ===")
        print(f"riders_processed: {total_riders}")
        print(f"pairs_found: {total_pairs}")
        print(f"pairs_with_stage_date: {total_matched_dates}")
        print(f"selected_for_update: {total_selected}")
        print(f"skipped_no_candidate_gt_100km: {total_skipped_no_candidate}")
        print(f"ignored_out_of_stage_calendar: {total_ignored_out}")
        print(f"total_stage_links_updated: {total_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
