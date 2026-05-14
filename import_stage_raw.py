#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from datetime import date, timezone
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


def _rider_strava_enabled(rider: dict) -> bool:
    # Backward-compatible support:
    # - enabled: false
    # - strava_enabled: false
    # - strava: { enabled: false }
    if rider.get("enabled", True) is False:
        return False
    if rider.get("strava_enabled", True) is False:
        return False
    strava_cfg = rider.get("strava")
    if isinstance(strava_cfg, dict) and strava_cfg.get("enabled", True) is False:
        return False
    return True


def _extract_start_hhmm_from_gpx(gpx_path: Path) -> str:
    # Fast path: scan only for the first <time> tag instead of full GPX parsing.
    try:
        with gpx_path.open("r", encoding="utf-8", errors="ignore") as fh:
            for _ in range(2000):
                line = fh.readline()
                if not line:
                    break
                m = re.search(r"<time>([^<]+)</time>", line)
                if not m:
                    continue
                raw = m.group(1).strip()
                dt = None
                if raw.endswith("Z"):
                    dt = __import__("datetime").datetime.fromisoformat(raw.replace("Z", "+00:00"))
                else:
                    dt = __import__("datetime").datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone().strftime("%H:%M")
    except Exception:
        return "-"
    return "-"


def ensure_stage_css(dataset_dir: Path) -> Path:
    out_dir = dataset_dir / "html"
    out_dir.mkdir(parents=True, exist_ok=True)
    style_path = out_dir / "style.css"
    style_path.write_text(
        """\
:root {
  --bg: #f4f7ff;
  --panel: #ffffff;
  --ink: #12253f;
  --muted: #496184;
  --line: #d5deee;
  --head: #e8eefb;
  --accent: #2f64d6;
}
body { margin: 0; background: var(--bg); color: var(--ink); font-family: "Segoe UI", Arial, sans-serif; }
.wrap { max-width: 1320px; margin: 24px auto; padding: 0 16px; }
h1 { margin: 0 0 10px 0; font-size: 28px; letter-spacing: .2px; }
p { margin: 0 0 12px; color: var(--muted); }
.meta-grid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); margin: 0 0 14px 0; }
.meta-card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; font-size: 13px; color: var(--muted); }
.meta-card b { color: var(--ink); }
.stage-nav {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 10px;
  margin: 10px 0 14px;
  font-size: 13px;
}
.stage-nav .left { justify-self: start; }
.stage-nav .center { justify-self: center; }
.stage-nav .right { justify-self: end; }
.stage-nav a { color: var(--accent); text-decoration: none; font-weight: 600; }
.tbl { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
table { border-collapse: collapse; width: 100%; }
thead th { position: sticky; top: 0; z-index: 2; background: var(--head); }
th, td { border-bottom: 1px solid var(--line); padding: 8px 9px; text-align: left; font-size: 12.5px; vertical-align: middle; }
tbody tr:nth-child(even) { background: #f9fbff; }
tr.team-sep td { border-top: 3px solid #9fb7e6; }
.week-sep td { border-top: 3px solid #5e7fbd; }
.start-midnight { color: #b00020; font-weight: 700; }
a { color: var(--accent); text-decoration: none; }
""",
        encoding="utf-8",
    )
    return style_path


def render_stage_html(dataset_dir: Path, stage_id: str) -> Path:
    ensure_stage_css(dataset_dir)
    riders_payload = json.loads((dataset_dir / "riders.json").read_text(encoding="utf-8"))
    riders = riders_payload["riders"]
    riders_by_id = {r["rider_id"]: r for r in riders}

    stages_payload = json.loads((dataset_dir / "stages.json").read_text(encoding="utf-8"))
    stages_list = stages_payload["stages"]
    stage_meta = next((s for s in stages_list if s["stage_id"] == stage_id), None)
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

    prev_stage = None
    next_stage = None
    for i, s in enumerate(stages_list):
        if s.get("stage_id") == stage_id:
            if i > 0:
                prev_stage = stages_list[i - 1].get("stage_id")
            if i < len(stages_list) - 1:
                next_stage = stages_list[i + 1].get("stage_id")
            break
    prev_link = f'<a href="{html.escape(prev_stage)}.html">← {html.escape(prev_stage)}</a>' if prev_stage else ""
    next_link = f'<a href="{html.escape(next_stage)}.html">{html.escape(next_stage)} →</a>' if next_stage else ""

    stage_payload = json.loads((dataset_dir / "stage_links" / f"{stage_id}.json").read_text(encoding="utf-8"))
    rows = stage_payload["activities"]
    eligible = 0
    with_activity = 0
    for row in rows:
        rider = riders_by_id.get(row["rider_id"], {})
        if _rider_strava_enabled(rider) and rider.get("strava_athlete_url"):
            eligible += 1
            if row.get("activity_url"):
                with_activity += 1
    total = len(rows)
    missing = max(eligible - with_activity, 0)

    body_rows: list[str] = []
    for row in rows:
        rider = riders_by_id.get(row["rider_id"], {})
        bib = rider.get("bib", "")
        name = rider.get("name", row["rider_id"])
        team = rider.get("team_name", "")
        nationality = rider.get("nationality", "")
        profile_url = rider.get("strava_athlete_url") if _rider_strava_enabled(rider) else None
        activity_url = row.get("activity_url")
        gpx_cell = "-"
        start_hhmm = "-"
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
                    start_hhmm = _extract_start_hhmm_from_gpx(gpx_path)

        row_class = ""
        try:
            bib_int = int(bib)
            if bib_int % 10 == 1 and bib_int != 1:
                row_class = ' class="team-sep"'
        except Exception:
            pass

        rider_page_rel = f"../riders/{row['rider_id']}.html"
        name_cell = f'<a href="{html.escape(rider_page_rel, quote=True)}">{html.escape(str(name))}</a>'

        body_rows.append(
            f"<tr{row_class}>"
            f"<td>{html.escape(str(bib))}</td>"
            f"<td>{name_cell}</td>"
            f"<td>{html.escape(str(team))}</td>"
            f"<td>{html.escape(str(nationality))}</td>"
            f"<td>{_link(profile_url, 'profile')}</td>"
            f"<td>{_link(activity_url, 'activity')}</td>"
            f"<td>{gpx_cell}</td>"
            f"<td{' class=\"start-midnight\"' if start_hhmm.startswith('00:') else ''}>{start_hhmm}</td>"
            "</tr>"
        )

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Giro 2026 - {html.escape(stage_id)}</title>
  <link rel="stylesheet" href="../style.css" />
  </head>
  <body>
  <div class="wrap">
  <h1>Stage {html.escape(stage_id)}</h1>
  <div class="meta-grid">
    <div class="meta-card"><b>Total riders:</b> {total}</div>
    <div class="meta-card"><b>Missing:</b> {missing}/{eligible}</div>
    <div class="meta-card"><b>Flight track:</b> {html.escape(flight_status)}</div>
    <div class="meta-card"><b>Flight source:</b> {flight_source_link}</div>
  </div>
  <div class="stage-nav">
    <span class="left">{prev_link}</span>
    <span class="center"><a href="../index.html">Back to stage index</a></span>
    <span class="right">{next_link}</span>
  </div>
  <div class="tbl"><table>
    <thead>
      <tr>
        <th>Bib</th><th>Name</th><th>Team</th><th>Nationality</th><th>Strava Profile</th><th>Stage Activity</th><th>GPX</th><th>Start</th>
      </tr>
    </thead>
    <tbody>
      {''.join(body_rows)}
    </tbody>
  </table></div>
  </div>
</body>
</html>
"""

    out_dir = dataset_dir / "html" / "stages"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stage_id}.html"
    out_path.write_text(html_out, encoding="utf-8")
    return out_path


def render_stage_index_html(dataset_dir: Path) -> Path:
    ensure_stage_css(dataset_dir)
    stages_payload = json.loads((dataset_dir / "stages.json").read_text(encoding="utf-8"))
    stages = stages_payload["stages"]
    riders_payload = json.loads((dataset_dir / "riders.json").read_text(encoding="utf-8"))
    riders = riders_payload["riders"]
    riders_by_id = {r["rider_id"]: r for r in riders}

    rows_html: list[str] = []
    for stage in stages:
        stage_id = stage["stage_id"]
        stage_file = dataset_dir / "stage_links" / f"{stage_id}.json"
        if stage_file.exists():
            payload = json.loads(stage_file.read_text(encoding="utf-8"))
            activities = payload.get("activities", [])
            eligible = 0
            found = 0
            total = len(activities)
            for a in activities:
                rider = riders_by_id.get(a.get("rider_id"), {})
                if _rider_strava_enabled(rider) and rider.get("strava_athlete_url"):
                    eligible += 1
                    if a.get("activity_url"):
                        found += 1
        else:
            found = 0
            total = 0
            eligible = 0
        missing = max(eligible - found, 0)
        route = f"{stage.get('start_city', '')} \u2192 {stage.get('finish_city', '')}"
        map_rel = f"maps/{stage_id}.html"
        map_abs = dataset_dir / "html" / "maps" / f"{stage_id}.html"
        map_cell = f'<a href="{html.escape(map_rel, quote=True)}">open</a>' if map_abs.exists() else "-"
        row_class = ""
        try:
            d = date.fromisoformat(str(stage.get("date", "")))
            if d.weekday() == 1:  # Tuesday
                row_class = ' class="week-sep"'
        except Exception:
            row_class = ""

        rows_html.append(
            f"<tr{row_class}>"
            f"<td>{html.escape(stage_id)}</td>"
            f"<td>{html.escape(str(stage.get('date', '')))}</td>"
            f"<td>{html.escape(route)}</td>"
            f"<td>{found}</td>"
            f"<td>{missing}</td>"
            f'<td><a href="stages/{html.escape(stage_id)}.html">open</a></td>'
            f"<td>{map_cell}</td>"
            "</tr>"
        )

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Giro 2026 Stage Pages</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <div class="wrap">
  <h1>Giro 2026 - Stage HTML Pages</h1>
  <p>Per-stage pages with rider info, Strava profile links, and stage activity links.</p>
  <div class="tbl"><table>
    <thead>
      <tr><th>Stage</th><th>Date</th><th>Route</th><th>Found</th><th>Missing</th><th>Page</th><th>Map</th></tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table></div>
  </div>
</body>
</html>
"""

    out_dir = dataset_dir / "html"
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

    if args.raw_file:
        raw_path = Path(args.raw_file)
    else:
        raw_upper = dataset_dir / "raw" / f"{args.stage_id}.txt"
        raw_lower = dataset_dir / "raw" / f"{args.stage_id.lower()}.txt"
        raw_path = raw_upper if raw_upper.exists() else raw_lower
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
