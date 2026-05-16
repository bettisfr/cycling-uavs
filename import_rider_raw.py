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
    ap.add_argument("--rider-id", required=True, help="e.g. B002")
    ap.add_argument("--html-file", default=None, help="Raw rider page HTML path")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--year", type=int, default=2026)
    args = ap.parse_args()

    base = Path(args.dataset_dir)
    if args.html_file:
        raw_path = Path(args.html_file)
    else:
        raw_path = base / "raw" / "riders" / f"{args.rider_id}.html"
    if not raw_path.exists():
        raise SystemExit(f"Raw rider file not found: {raw_path}")

    html = raw_path.read_text(encoding="utf-8", errors="ignore")
    pairs = extract_pairs(html, today=date(args.year, 5, 16))

    stages = json.loads((base / "stages.json").read_text(encoding="utf-8"))["stages"]
    stage_by_date = {s["date"]: s["stage_id"] for s in stages}
    stage_dates = set(stage_by_date.keys())

    # Group candidates by day (some riders may have more than one activity/day).
    by_day: dict[str, list[dict[str, str | float | None]]] = {}
    ignored_out_of_stage_calendar = 0
    for row in pairs:
        d = str(row["date"])
        if d not in stage_dates:
            ignored_out_of_stage_calendar += 1
            continue
        by_day.setdefault(d, []).append(row)

    stage_meta_by_id = {s["stage_id"]: s for s in stages}

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
        row = next((a for a in payload.get("activities", []) if a.get("rider_id") == args.rider_id), None)
        if not row:
            continue
        new_url = f"https://www.strava.com/activities/{aid}"
        if row.get("activity_url") != new_url:
            row["activity_url"] = new_url
            row["status"] = "found_public"
            p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            updated += 1

    print(f"rider_id={args.rider_id}")
    print(f"raw_file={raw_path}")
    print(f"pairs_found={len(pairs)}")
    print(f"ignored_out_of_stage_calendar={ignored_out_of_stage_calendar}")
    print(f"pairs_with_stage_date={matched_dates}")
    print(f"selected_for_update={selected}")
    print(f"skipped_no_candidate_gt_100km={skipped_no_candidate}")
    print(f"stage_links_updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
