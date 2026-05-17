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
    low = label.lower()
    if low.startswith("today") or low.startswith("oggi"):
        return today.isoformat()
    if low.startswith("yesterday") or low.startswith("ieri"):
        return (today - timedelta(days=1)).isoformat()
    m = DATE_RE.match(label)
    if m:
        return f"2026-05-{int(m.group(1)):02d}"
    return None


def extract_pairs(
    html: str,
    today: date,
    expected_owner_paths: set[str] | None = None,
) -> list[dict[str, str | float | None]]:
    soup = BeautifulSoup(html, "html.parser")
    entries = soup.select(".CQdSY")
    if not entries:
        return []

    out: list[dict[str, str | float | None]] = []
    for entry in entries:
        if expected_owner_paths:
            owner_link = entry.select_one('a[data-testid="owners-name"]')
            owner_href = owner_link.get("href", "").strip() if owner_link else ""
            if owner_href not in expected_owner_paths:
                continue
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
        date_node = entry.select_one('time[data-testid="date_at_time"]')
        if not date_node:
            continue
        d = None
        dt_attr = date_node.get("datetime")
        if isinstance(dt_attr, str) and len(dt_attr) >= 10:
            # Prefer absolute date from ISO-like datetime attribute when available.
            d = dt_attr[:10]
        if not d:
            d = parse_date_label(date_node.get_text(" ", strip=True), today)
        if not d:
            continue
        dist_km = None
        # Preferred: structured lookup from activity stats list.
        for li in entry.select("ul.fmAtV li"):
            label = li.select_one("span.U5UN2")
            value = li.select_one("div.vNsSU")
            if not label or not value:
                continue
            if label.get_text(" ", strip=True).lower().startswith("distance"):
                raw = value.get_text(" ", strip=True).replace(",", "")
                m_num = re.search(r"([0-9]+(?:\.[0-9]+)?)", raw)
                if m_num:
                    dist_km = float(m_num.group(1))
                break
        # Fallback: text regex.
        if dist_km is None:
            txt = " ".join(entry.get_text(" ", strip=True).split())
            m_dist = DIST_RE.search(txt)
            dist_km = float(m_dist.group(1)) if m_dist else None
        out.append({"date": d, "activity_id": aid, "distance_km": dist_km})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Import rider profile raw HTML and map activities to stages by date.")
    ap.add_argument("--rider-id", default=None, help="e.g. B002")
    ap.add_argument("--all", action="store_true", help="Process all raw rider files in dataset raw/riders/")
    ap.add_argument(
        "--include-yesterday",
        action="store_true",
        help="Allow mapping up to yesterday (default: strictly before yesterday).",
    )
    ap.add_argument(
        "--include-today",
        action="store_true",
        help="Allow mapping up to today.",
    )
    ap.add_argument("--html-file", default=None, help="Raw rider page HTML path")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--year", type=int, default=2026)
    args = ap.parse_args()

    base = Path(args.dataset_dir)
    stages = json.loads((base / "stages.json").read_text(encoding="utf-8"))["stages"]
    stage_by_date = {s["date"]: s["stage_id"] for s in stages}
    today = date.today()
    today_iso = today.isoformat()
    yesterday_iso = (today - timedelta(days=1)).isoformat()
    if args.include_today:
        stage_dates = {d for d in stage_by_date.keys() if d <= today_iso}
        cutoff_label = f"<= today ({today_iso})"
    elif args.include_yesterday:
        stage_dates = {d for d in stage_by_date.keys() if d <= yesterday_iso}
        cutoff_label = f"<= yesterday ({yesterday_iso})"
    else:
        stage_dates = {d for d in stage_by_date.keys() if d < yesterday_iso}
        cutoff_label = f"< yesterday ({yesterday_iso})"
    stage_meta_by_id = {s["stage_id"]: s for s in stages}
    overrides_path = base / "stage_links" / "manual_overrides.json"
    overrides = {}
    if overrides_path.exists():
        overrides = json.loads(overrides_path.read_text(encoding="utf-8"))

    riders_payload = json.loads((base / "riders.json").read_text(encoding="utf-8")).get("riders", [])
    riders_by_id = {r.get("rider_id"): r for r in riders_payload if isinstance(r, dict)}

    def stage_num(stage_id: str) -> int:
        try:
            return int(str(stage_id).lstrip("S"))
        except Exception:
            return -1

    def withdraw_stage_of(rider_id: str) -> int:
        meta = riders_by_id.get(rider_id, {})
        value = meta.get("withdraw_stage", -1) if isinstance(meta, dict) else -1
        try:
            v = int(value)
            return v if v >= 0 else -1
        except Exception:
            return -1

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
        expected_owner_paths: set[str] = set()
        rider_meta = riders_by_id.get(rider_id, {})
        athlete_url = rider_meta.get("strava_athlete_url") if isinstance(rider_meta, dict) else None
        if isinstance(athlete_url, str) and athlete_url.strip():
            m = re.search(r"strava\.com/(pros|athletes)/([^/?#]+)", athlete_url)
            if m:
                expected_owner_paths.add(f"/{m.group(1)}/{m.group(2)}")
                # Strava feed often uses /athletes/<id> for pros pages.
                if m.group(1) == "pros":
                    expected_owner_paths.add(f"/athletes/{m.group(2)}")
        pairs = extract_pairs(html, today=date.today(), expected_owner_paths=expected_owner_paths or None)

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
        selected_rows: list[tuple[str, str, str, str, bool]] = []
        for d, candidates in by_day.items():
            sid = stage_by_date.get(d)
            if not sid:
                continue
            ws = withdraw_stage_of(rider_id)
            if ws >= 0 and stage_num(sid) > ws:
                # Rider withdrawn after stage ws, ignore later stages.
                continue
            matched_dates += 1

            # Selection rule:
            # - road stages (>100km): keep candidates with distance > 100km, then pick max distance
            # - short stages (<=100km): pick max distance among all parsed distances
            # - if none has parsed distance, fallback to first candidate
            stage_distance = stage_meta_by_id.get(sid, {}).get("distance_km")
            if not isinstance(stage_distance, (int, float)):
                stage_distance = None
            enforce_long_filter = bool(stage_distance is None or float(stage_distance) > 100.0)

            valid: list[dict[str, str | float | None]] = []
            for c in candidates:
                dist = c.get("distance_km")
                if isinstance(dist, (int, float)):
                    if (not enforce_long_filter) or float(dist) > 100.0:
                        valid.append(c)
            if not valid:
                # On long stages we require >100km; if nothing matches, skip update.
                if enforce_long_filter:
                    skipped_no_candidate += 1
                    continue
                chosen = candidates[0]
            else:
                chosen = max(valid, key=lambda c: float(c["distance_km"]))  # type: ignore[index]
            selected += 1
            aid = str(chosen["activity_id"])
            p = base / "stage_links" / f"{sid}.json"
            if not p.exists():
                continue
            payload = json.loads(p.read_text(encoding="utf-8"))
            row = next((a for a in payload.get("activities", []) if a.get("rider_id") == rider_id), None)
            if not row:
                continue
            chosen_km = chosen.get("distance_km")
            km_text = f"{float(chosen_km):.2f}" if isinstance(chosen_km, (int, float)) else "-"
            new_url = f"https://www.strava.com/activities/{aid}"
            is_new = row.get("activity_url") != new_url
            selected_rows.append((sid, d, str(chosen["activity_id"]), km_text, is_new))
            if bool(row.get("locked")):
                continue
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
            if bool(row.get("locked")):
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
            f"fallback_first={skipped_no_candidate} "
            f"ignored_out={ignored_out_of_stage_calendar}"
        )
        for sid, d, aid, km_text, is_new in sorted(selected_rows, key=lambda x: x[1], reverse=True):
            suffix = " [new]" if is_new else ""
            print(f"  - {sid} {d} -> {aid} ({km_text} km){suffix}")

    if args.all:
        print()
        print("=== Import Summary ===")
        print(f"stage_date_cutoff: {cutoff_label}")
        print(f"riders_processed: {total_riders}")
        print(f"pairs_found: {total_pairs}")
        print(f"pairs_with_stage_date: {total_matched_dates}")
        print(f"selected_for_update: {total_selected}")
        print(f"fallback_selected_without_distance: {total_skipped_no_candidate}")
        print(f"ignored_out_of_stage_calendar: {total_ignored_out}")
        print(f"total_stage_links_updated: {total_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
