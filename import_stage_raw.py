#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from pathlib import Path


ENTRY_RE = re.compile(
    r'<div class="x35YV" data-testid="entry">(.*?)</div><button class="i_Upj zyqUR _vKTN"',
    re.S,
)
ATHLETE_RE = re.compile(r'href="(/pros/[^"]+|/athletes/\d+)"')
ACTIVITY_RE = re.compile(r'href="(/activities/\d+)"')
NAME_RE = re.compile(r'<div class="G1c7V"><a href="[^"]+">(.*?)</a><div class="RTdgF">', re.S)


def normalize_name(value: str) -> str:
    text = value.strip().lower()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_slug(slug: str) -> str:
    s = slug.strip().lower()
    s = re.sub(r"-\d+$", "", s)
    return s


def parse_entries(raw_html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for block in ENTRY_RE.findall(raw_html):
        athlete_m = ATHLETE_RE.search(block)
        activity_m = ACTIVITY_RE.search(block)
        name_m = NAME_RE.search(block)
        if not athlete_m or not activity_m or not name_m:
            continue
        name = re.sub(r"\s+", " ", name_m.group(1)).strip()
        rows.append(
            {
                "name": name,
                "name_norm": normalize_name(name),
                "athlete_path": athlete_m.group(1),
                "activity_url": f"https://www.strava.com{activity_m.group(1)}",
            }
        )
    return rows


def build_indexes(riders: list[dict]) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    by_athlete_id: dict[str, str] = {}
    by_pros_slug: dict[str, str] = {}
    name_to_rider_ids: dict[str, list[str]] = {}

    by_name_all: dict[str, str] = {}
    for rider in riders:
        rider_id = rider["rider_id"]
        rider_name_norm = normalize_name(rider["name"])
        name_to_rider_ids.setdefault(rider_name_norm, []).append(rider_id)
        by_name_all[rider_name_norm] = rider_id
        url = rider.get("strava_athlete_url")
        if not url:
            continue

        m_id = re.search(r"/athletes/(\d+)", url)
        if m_id:
            by_athlete_id[m_id.group(1)] = rider_id

        m_slug = re.search(r"/pros/([^/?#]+)", url)
        if m_slug:
            by_pros_slug[normalize_slug(m_slug.group(1))] = rider_id

    by_name_unique = {k: v[0] for k, v in name_to_rider_ids.items() if len(v) == 1}
    return by_athlete_id, by_pros_slug, by_name_unique, by_name_all


def resolve_by_name_fuzzy(name_norm: str, by_name_all: dict[str, str]) -> str | None:
    # Conservative fallback for display-name variants:
    # - added middle names/surnames
    # - short first-name aliases (e.g. Larry/Lawrence)
    tokens = name_norm.split()
    if len(tokens) < 2:
        return None
    first = tokens[0]
    last = tokens[-1]
    first_alias = {
        "larry": "lawrence",
    }
    first_cmp = first_alias.get(first, first)

    candidates: list[str] = []
    for rider_name_norm, rider_id in by_name_all.items():
        rt = rider_name_norm.split()
        if len(rt) < 2:
            continue
        rfirst = rt[0]
        rlast = rt[-1]
        same_last = rlast == last or rlast in tokens or last in rt
        compatible_first = (
            rfirst == first_cmp or rfirst.startswith(first_cmp) or first_cmp.startswith(rfirst)
        )
        subset_name = all(t in tokens for t in rt)
        subset_rider = all(t in rt for t in tokens)
        if (same_last and compatible_first) or subset_name or subset_rider:
            candidates.append(rider_id)

    if len(set(candidates)) == 1:
        return candidates[0]
    return None


def resolve_rider_id(
    athlete_path: str,
    name_norm: str,
    by_athlete_id: dict[str, str],
    by_pros_slug: dict[str, str],
    by_name_unique: dict[str, str],
    by_name_all: dict[str, str],
) -> str | None:
    m_id = re.match(r"/athletes/(\d+)$", athlete_path)
    if m_id:
        rid = by_athlete_id.get(m_id.group(1))
        if rid:
            return rid

    m_pro = re.match(r"/pros/([^/?#]+)$", athlete_path)
    if m_pro:
        slug_raw = m_pro.group(1)
        slug = normalize_slug(slug_raw)
        rid = by_pros_slug.get(slug)
        if rid:
            return rid
        # Some /pros slugs include trailing athlete ids.
        m_embedded_id = re.search(r"-(\d+)$", slug_raw)
        if m_embedded_id:
            rid = by_athlete_id.get(m_embedded_id.group(1))
            if rid:
                return rid

    rid = by_name_unique.get(name_norm)
    if rid:
        return rid
    rid = resolve_by_name_fuzzy(name_norm, by_name_all)
    if rid:
        return rid

    return None


def _link(url: str | None, label: str) -> str:
    if not url:
        return "-"
    safe = html.escape(url, quote=True)
    return f'<a href="{safe}" target="_blank" rel="noopener noreferrer">{label}</a>'


def render_stage_html(dataset_dir: Path, stage_id: str) -> Path:
    riders_payload = json.loads((dataset_dir / "riders.json").read_text(encoding="utf-8"))
    riders = riders_payload["riders"]
    riders_by_id = {r["rider_id"]: r for r in riders}

    stages_payload = json.loads((dataset_dir / "stages.json").read_text(encoding="utf-8"))
    stage_meta = next((s for s in stages_payload["stages"] if s["stage_id"] == stage_id), None)
    title = stage_id
    if stage_meta:
        start_city = stage_meta.get("start_city", "")
        finish_city = stage_meta.get("finish_city", "")
        date = stage_meta.get("date", "")
        title = f"{stage_id} - {start_city} to {finish_city} ({date})"
    flight = stage_meta.get("flight", {}) if stage_meta else {}
    flight_status = str(flight.get("track_status", "not_checked"))
    flight_csv = flight.get("track_csv_path")
    flight_sources = flight.get("source_urls", [])

    flight_csv_link = "-"
    if flight_csv:
        try:
            csv_abs = Path(str(flight_csv))
            if csv_abs.exists():
                rel = csv_abs.relative_to(dataset_dir.parent)
                flight_csv_link = f'<a href="{html.escape(str(rel), quote=True)}" target="_blank" rel="noopener noreferrer">csv</a>'
            else:
                flight_csv_link = html.escape(str(flight_csv))
        except Exception:
            flight_csv_link = html.escape(str(flight_csv))

    flight_source_link = "-"
    if isinstance(flight_sources, list) and flight_sources:
        src = str(flight_sources[0])
        flight_source_link = f'<a href="{html.escape(src, quote=True)}" target="_blank" rel="noopener noreferrer">source</a>'

    stage_payload = json.loads((dataset_dir / "stage_links" / f"{stage_id}.json").read_text(encoding="utf-8"))
    rows = stage_payload["activities"]
    with_activity = sum(1 for row in rows if row.get("activity_url"))
    total = len(rows)
    missing = total - with_activity

    body_rows: list[str] = []
    for row in rows:
        rider = riders_by_id.get(row["rider_id"], {})
        bib = rider.get("bib", "")
        name = rider.get("name", row["rider_id"])
        team = rider.get("team_name", "")
        nationality = rider.get("nationality", "")
        status = row.get("status", "")
        profile_url = rider.get("strava_athlete_url")
        activity_url = row.get("activity_url")
        gpx_cell = "-"
        if activity_url:
            m = re.search(r"/activities/(\d+)", activity_url)
            if m:
                aid = m.group(1)
                gpx_path = (
                    dataset_dir
                    / "courses"
                    / stage_id
                    / f"{row['rider_id']}__activity_{aid}.gpx"
                )
                if gpx_path.exists() and gpx_path.stat().st_size > 0:
                    safe_path = html.escape(str(gpx_path), quote=True)
                    gpx_cell = f'<a href="file://{safe_path}" target="_blank" rel="noopener noreferrer">yes</a>'

        row_class = ""
        try:
            bib_int = int(bib)
            if bib_int % 10 == 1 and bib_int != 1:
                row_class = ' class="team-sep"'
        except Exception:
            pass

        body_rows.append(
            f"<tr{row_class}>"
            f"<td>{html.escape(str(bib))}</td>"
            f"<td>{html.escape(str(name))}</td>"
            f"<td>{html.escape(str(team))}</td>"
            f"<td>{html.escape(str(nationality))}</td>"
            f"<td>{html.escape(str(status))}</td>"
            f"<td>{_link(profile_url, 'profile')}</td>"
            f"<td>{_link(activity_url, 'activity')}</td>"
            f"<td>{gpx_cell}</td>"
            "</tr>"
        )

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; }}
    h1 {{ margin-bottom: 4px; }}
    .meta {{ color: #555; margin-bottom: 16px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 7px; text-align: left; font-size: 13px; }}
    th {{ background: #f5f5f5; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    tr.team-sep td {{ border-top: 3px solid #999; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <div class="meta">Total riders: {total} | Activity links found: {with_activity} | Missing: {missing}</div>
  <div class="meta">Flight track: {html.escape(flight_status)} | CSV: {flight_csv_link} | Source: {flight_source_link}</div>
  <p><a href="index.html">Back to stage index</a></p>
  <table>
    <thead>
      <tr>
        <th>Bib</th><th>Name</th><th>Team</th><th>Nationality</th><th>Status</th><th>Strava Profile</th><th>Stage Activity</th><th>GPX</th>
      </tr>
    </thead>
    <tbody>
      {''.join(body_rows)}
    </tbody>
  </table>
</body>
</html>
"""

    out_dir = dataset_dir / "html" / "stages"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stage_id}.html"
    out_path.write_text(html_out, encoding="utf-8")
    return out_path


def render_stage_index_html(dataset_dir: Path) -> Path:
    stages_payload = json.loads((dataset_dir / "stages.json").read_text(encoding="utf-8"))
    stages = stages_payload["stages"]

    rows_html: list[str] = []
    for stage in stages:
        stage_id = stage["stage_id"]
        stage_file = dataset_dir / "stage_links" / f"{stage_id}.json"
        if stage_file.exists():
            payload = json.loads(stage_file.read_text(encoding="utf-8"))
            activities = payload.get("activities", [])
            found = sum(1 for a in activities if a.get("activity_url"))
            total = len(activities)
        else:
            found = 0
            total = 0
        missing = max(total - found, 0)
        route = f"{stage.get('start_city', '')} \u2192 {stage.get('finish_city', '')}"
        map_rel = f"../maps/map_{stage_id}.html"
        map_abs = dataset_dir / "html" / "maps" / f"map_{stage_id}.html"
        map_cell = f'<a href="{html.escape(map_rel, quote=True)}">open</a>' if map_abs.exists() else "-"
        rows_html.append(
            "<tr>"
            f"<td>{html.escape(stage_id)}</td>"
            f"<td>{html.escape(str(stage.get('date', '')))}</td>"
            f"<td>{html.escape(route)}</td>"
            f"<td>{found}</td>"
            f"<td>{missing}</td>"
            f'<td><a href="{html.escape(stage_id)}.html">open</a></td>'
            f"<td>{map_cell}</td>"
            "</tr>"
        )

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Giro 2026 Stage Pages</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #f5f5f5; }}
    tr:nth-child(even) {{ background: #fafafa; }}
  </style>
</head>
<body>
  <h1>Giro 2026 - Stage HTML Pages</h1>
  <p>Per-stage pages with rider info, Strava profile links, and stage activity links.</p>
  <table>
    <thead>
      <tr><th>Stage</th><th>Date</th><th>Route</th><th>Found</th><th>Missing</th><th>Page</th><th>Map</th></tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</body>
</html>
"""

    out_dir = dataset_dir / "html" / "stages"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html_out, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Strava activities from raw stage HTML into stage_links JSON.")
    parser.add_argument("--stage-id", required=True, help="Stage id, e.g. S02")
    parser.add_argument("--dataset-dir", default="giro_2026")
    parser.add_argument("--raw-file", default=None, help="Raw HTML path (default: <dataset-dir>/raw/sXX.txt)")
    args = parser.parse_args()

    repo = Path.cwd()
    dataset_dir = repo / args.dataset_dir
    stage_path = dataset_dir / "stage_links" / f"{args.stage_id}.json"
    riders_path = dataset_dir / "riders.json"

    raw_path = Path(args.raw_file) if args.raw_file else dataset_dir / "raw" / f"{args.stage_id.lower()}.txt"
    if not raw_path.exists():
        raise SystemExit(f"Raw file not found: {raw_path}")
    if not stage_path.exists():
        raise SystemExit(f"Stage file not found: {stage_path}")

    raw_html = raw_path.read_text(encoding="utf-8", errors="ignore")
    entries = parse_entries(raw_html)

    riders_payload = json.loads(riders_path.read_text(encoding="utf-8"))
    riders = riders_payload["riders"]
    by_athlete_id, by_pros_slug, by_name_unique, by_name_all = build_indexes(riders)

    stage_payload = json.loads(stage_path.read_text(encoding="utf-8"))
    activities = stage_payload["activities"]
    by_rider_id = {row["rider_id"]: row for row in activities}

    matched = 0
    unmatched: list[dict[str, str]] = []
    seen_rider_ids: set[str] = set()

    for entry in entries:
        rider_id = resolve_rider_id(
            entry["athlete_path"],
            entry["name_norm"],
            by_athlete_id,
            by_pros_slug,
            by_name_unique,
            by_name_all,
        )
        if not rider_id:
            unmatched.append(entry)
            continue

        row = by_rider_id.get(rider_id)
        if not row:
            unmatched.append(entry)
            continue

        row["activity_url"] = entry["activity_url"]
        row["status"] = "found_public"
        matched += 1
        seen_rider_ids.add(rider_id)

    stage_path.write_text(json.dumps(stage_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_path = render_stage_html(dataset_dir, args.stage_id)
    index_path = render_stage_index_html(dataset_dir)

    with_url = sum(1 for a in activities if a.get("activity_url"))
    print(f"stage={args.stage_id}")
    print(f"entries={len(entries)}")
    print(f"matched_now={matched}")
    print(f"distinct_riders_matched={len(seen_rider_ids)}")
    print(f"total_with_activity_url={with_url}")
    print(f"html_updated={html_path}")
    print(f"index_updated={index_path}")
    print(f"unmatched={len(unmatched)}")
    if unmatched:
        print("unmatched_sample:")
        for row in unmatched[:20]:
            print(f"- {row['name']} | {row['athlete_path']} | {row['activity_url']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
