#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, timezone
from pathlib import Path


ENTRY_RE = re.compile(
    r'<div class="x35YV" data-testid="entry">(.*?)</div><button class="i_Upj zyqUR _vKTN"',
    re.S,
)
ATHLETE_RE = re.compile(r'href="(/pros/[^"]+|/athletes/\d+)"')
ACTIVITY_RE = re.compile(r'href="(/activities/\d+)"')
NAME_RE = re.compile(r'<div class="G1c7V"><a href="[^"]+">(.*?)</a><div class="RTdgF">', re.S)
PROS_ALIAS_TO_CANONICAL = {
    "ilbandito": "2004466",
}


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
        alias = PROS_ALIAS_TO_CANONICAL.get(slug)
        if alias:
            rid = by_pros_slug.get(normalize_slug(alias))
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


def _stage_num(stage_id: str) -> int:
    try:
        return int(str(stage_id).lstrip("S"))
    except Exception:
        return -1


def _withdraw_stage(rider: dict) -> int:
    try:
        ws = int(rider.get("withdraw_stage", -1))
        return ws if ws >= 0 else -1
    except Exception:
        return -1


def _is_withdrawn_for_stage(rider: dict, stage_id: str) -> bool:
    ws = _withdraw_stage(rider)
    if ws < 0:
        return False
    sn = _stage_num(stage_id)
    return sn > ws


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


def _extract_distance_km_from_gpx(gpx_path: Path) -> str:
    try:
        root = ET.parse(gpx_path).getroot()
        pts: list[tuple[float, float]] = []
        for p in root.iter():
            if not p.tag.endswith("trkpt"):
                continue
            lat = p.attrib.get("lat")
            lon = p.attrib.get("lon")
            if lat is None or lon is None:
                continue
            pts.append((float(lat), float(lon)))
        if len(pts) < 2:
            return "-"

        def h_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
            r = 6371000.0
            p1 = math.radians(lat1)
            p2 = math.radians(lat2)
            dp = math.radians(lat2 - lat1)
            dl = math.radians(lon2 - lon1)
            a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * r * math.asin(math.sqrt(a))

        dist_km = 0.0
        for i in range(1, len(pts)):
            a = pts[i - 1]
            b = pts[i]
            dist_km += h_m(a[0], a[1], b[0], b[1]) / 1000.0
        return f"{dist_km:.1f}"
    except Exception:
        return "-"


def _stage_gpx_metrics(dataset_dir: Path, stage_id: str) -> tuple[float | None, int | None]:
    stage_dir = dataset_dir / "courses" / stage_id
    gpx_files = sorted(stage_dir.glob("B*__activity_*.gpx"))
    if not gpx_files:
        return None, None
    gpx_path = gpx_files[0]
    try:
        root = ET.parse(gpx_path).getroot()
        pts: list[tuple[float, float, float | None]] = []
        for p in root.iter():
            if not p.tag.endswith("trkpt"):
                continue
            lat = p.attrib.get("lat")
            lon = p.attrib.get("lon")
            if lat is None or lon is None:
                continue
            ele = None
            for c in p:
                if c.tag.endswith("ele"):
                    try:
                        ele = float((c.text or "").strip())
                    except Exception:
                        ele = None
                    break
            pts.append((float(lat), float(lon), ele))
        if len(pts) < 2:
            return None, None

        def h_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
            r = 6371000.0
            p1 = math.radians(lat1)
            p2 = math.radians(lat2)
            dp = math.radians(lat2 - lat1)
            dl = math.radians(lon2 - lon1)
            a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * r * math.asin(math.sqrt(a))

        dist_km = 0.0
        elev_gain = 0.0
        for i in range(1, len(pts)):
            a = pts[i - 1]
            b = pts[i]
            dist_km += h_m(a[0], a[1], b[0], b[1]) / 1000.0
            if a[2] is not None and b[2] is not None and b[2] > a[2]:
                elev_gain += b[2] - a[2]
        return round(dist_km, 1), int(round(elev_gain))
    except Exception:
        return None, None


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
.stage-images { display: grid; grid-template-columns: repeat(auto-fit,minmax(360px,1fr)); gap: 12px; margin: 0 0 14px 0; }
.stage-images figure { margin: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 8px; }
.stage-images img { width: 100%; height: auto; border-radius: 8px; display: block; }
.stage-images figcaption { margin-top: 6px; font-size: 12px; color: var(--muted); }
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
.missing-activity { color: #b00020; font-weight: 700; }
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
    gpx_distance_km, gpx_elev_gain_m = _stage_gpx_metrics(dataset_dir, stage_id)
    length_text = "-"
    if gpx_distance_km is not None:
        length_text = f"{gpx_distance_km:.1f}"
    elev_text = str(gpx_elev_gain_m) if gpx_elev_gain_m is not None else "-"
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

    assets_dir = dataset_dir / "html" / "stages" / "assets" / stage_id
    plan_img = assets_dir / "planimetry.png"
    elev_img = assets_dir / "elevation.png"
    images_block = ""
    if plan_img.exists() or elev_img.exists():
        parts: list[str] = []
        if plan_img.exists():
            parts.append(
                f'<figure><img src="assets/{html.escape(stage_id)}/planimetry.png" alt="Stage planimetry" /><figcaption>Planimetry</figcaption></figure>'
            )
        if elev_img.exists():
            parts.append(
                f'<figure><img src="assets/{html.escape(stage_id)}/elevation.png" alt="Stage elevation profile" /><figcaption>Elevation Profile</figcaption></figure>'
            )
        images_block = f'<div class="stage-images">{"".join(parts)}</div>'

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
    stage_payload_changed = False
    eligible = 0
    with_activity = 0
    for row in rows:
        rider = riders_by_id.get(row["rider_id"], {})
        if _rider_strava_enabled(rider) and rider.get("strava_athlete_url") and not _is_withdrawn_for_stage(rider, stage_id):
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
        withdrawn_now = _is_withdrawn_for_stage(rider, stage_id)
        profile_url = rider.get("strava_athlete_url") if (_rider_strava_enabled(rider) and not withdrawn_now) else None
        activity_url = row.get("activity_url")
        has_profile = (_rider_strava_enabled(rider) and bool(rider.get("strava_athlete_url")) and not withdrawn_now)
        if withdrawn_now:
            activity_url = None
        gpx_cell = "-"
        start_hhmm = "-"
        gpx_km = "-"
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
                    gpx_href = html.escape(f"../../courses/{stage_id}/{gpx_path.name}", quote=True)
                    gpx_cell = f'<a href="{gpx_href}" target="_blank" rel="noopener noreferrer">yes</a>'
                    start_hhmm = row.get("gpx_start_hhmm") or "-"
                    gpx_km = row.get("gpx_km") or "-"
                    if start_hhmm == "-" or gpx_km == "-":
                        start_hhmm = _extract_start_hhmm_from_gpx(gpx_path)
                        gpx_km = _extract_distance_km_from_gpx(gpx_path)
                        row["gpx_start_hhmm"] = start_hhmm if start_hhmm != "-" else None
                        row["gpx_km"] = gpx_km if gpx_km != "-" else None
                        row["gpx_path"] = str(
                            Path(dataset_dir.name) / "courses" / stage_id / f"{row['rider_id']}__activity_{aid}.gpx"
                        )
                        stage_payload_changed = True
                else:
                    if row.get("gpx_start_hhmm") is not None or row.get("gpx_km") is not None:
                        row["gpx_start_hhmm"] = None
                        row["gpx_km"] = None
                        stage_payload_changed = True

        row_class = ""
        try:
            bib_int = int(bib)
            if bib_int % 10 == 1 and bib_int != 1:
                row_class = ' class="team-sep"'
        except Exception:
            pass

        rider_page_rel = f"../riders/{row['rider_id']}.html"
        name_cell = f'<a href="{html.escape(rider_page_rel, quote=True)}">{html.escape(str(name))}</a>'

        activity_cell = _link(activity_url, "activity")
        if not activity_url and has_profile:
            activity_cell = '<span class="missing-activity">missing</span>'

        body_rows.append(
            f"<tr{row_class}>"
            f"<td>{html.escape(str(bib))}</td>"
            f"<td>{name_cell}</td>"
            f"<td>{html.escape(str(team))}</td>"
            f"<td>{html.escape(str(nationality))}</td>"
            f"<td>{_link(profile_url, 'profile')}</td>"
            f"<td>{activity_cell}</td>"
            f"<td>{gpx_cell}</td>"
            f"<td{' class=\"start-midnight\"' if start_hhmm.startswith('00:') else ''}>{start_hhmm}</td>"
            f"<td>{gpx_km}</td>"
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
    <div class="meta-card"><b>Flight:</b> {html.escape(flight_status)} ({flight_source_link})</div>
    <div class="meta-card"><b>Length (km):</b> {length_text}</div>
    <div class="meta-card"><b>Elevation gain (m):</b> {elev_text}</div>
  </div>
  {images_block}
  <div class="stage-nav">
    <span class="left">{prev_link}</span>
    <span class="center"><a href="../index.html">Back to stage index</a></span>
    <span class="right">{next_link}</span>
  </div>
  <div class="tbl"><table>
    <thead>
      <tr>
        <th>Bib</th><th>Name</th><th>Team</th><th>Nationality</th><th>Strava Profile</th><th>Stage Activity</th><th>GPX</th><th>Start</th><th>Km</th>
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
    if stage_payload_changed:
        (dataset_dir / "stage_links" / f"{stage_id}.json").write_text(
            json.dumps(stage_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
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
                if _rider_strava_enabled(rider) and rider.get("strava_athlete_url") and not _is_withdrawn_for_stage(rider, stage_id):
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
    parser.add_argument("--download-gpx", action="store_true", default=True, help="Download matched GPX to pool and link into stage folder.")
    parser.add_argument("--local-tz", default="Europe/Rome")
    args = parser.parse_args()

    repo = Path.cwd()
    dataset_dir = repo / args.dataset_dir
    stage_path = dataset_dir / "stage_links" / f"{args.stage_id}.json"
    riders_path = dataset_dir / "riders.json"

    if args.raw_file:
        raw_path = Path(args.raw_file)
    else:
        # Preferred: giro_2026/raw/stages/SXX.txt
        raw_upper_new = dataset_dir / "raw" / "stages" / f"{args.stage_id}.txt"
        raw_lower_new = dataset_dir / "raw" / "stages" / f"{args.stage_id.lower()}.txt"
        # Backward compatibility: giro_2026/raw/SXX.txt
        raw_upper_old = dataset_dir / "raw" / f"{args.stage_id}.txt"
        raw_lower_old = dataset_dir / "raw" / f"{args.stage_id.lower()}.txt"
        if raw_upper_new.exists():
            raw_path = raw_upper_new
        elif raw_lower_new.exists():
            raw_path = raw_lower_new
        elif raw_upper_old.exists():
            raw_path = raw_upper_old
        else:
            raw_path = raw_lower_old
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
    gpx_store_dir = dataset_dir / "gpx_store"
    gpx_store_dir.mkdir(parents=True, exist_ok=True)
    stage_courses_dir = dataset_dir / "courses" / args.stage_id
    stage_courses_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = repo / "strava_session_cookie.txt"
    cookie = cookie_path.read_text(encoding="utf-8").strip() if cookie_path.exists() else ""

    matched = 0
    updated = 0
    locked_skipped = 0
    gpx_new = 0
    gpx_existing = 0
    gpx_fail = 0
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
        if bool(row.get("locked")):
            locked_skipped += 1
            continue

        if row.get("activity_url") != entry["activity_url"] or row.get("status") != "found_public":
            row["activity_url"] = entry["activity_url"]
            row["status"] = "found_public"
            updated += 1
        matched += 1
        seen_rider_ids.add(rider_id)

        if args.download_gpx:
            m = re.search(r"/activities/(\d+)", entry["activity_url"])
            if not m:
                continue
            aid = m.group(1)
            name = f"{rider_id}__activity_{aid}.gpx"
            store_path = gpx_store_dir / name
            stage_link = stage_courses_dir / name

            ok = False
            if store_path.exists() and store_path.stat().st_size > 0:
                ok = True
                gpx_existing += 1
            elif cookie:
                cmd = [
                    "/home/fra/pyvenv/bin/python",
                    str(repo / "lib" / "strava_to_gpx.py"),
                    "--session-cookie",
                    cookie,
                    "--local-tz",
                    args.local_tz,
                    entry["activity_url"],
                    "-o",
                    str(store_path),
                ]
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode == 0 and store_path.exists() and store_path.stat().st_size > 0:
                    ok = True
                    gpx_new += 1
                else:
                    gpx_fail += 1
            else:
                gpx_fail += 1

            if ok:
                if stage_link.exists() or stage_link.is_symlink():
                    stage_link.unlink(missing_ok=True)
                try:
                    stage_link.symlink_to(Path("..") / ".." / "gpx_store" / name)
                except Exception:
                    shutil.copy2(store_path, stage_link)

    stage_path.write_text(json.dumps(stage_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_path = render_stage_html(dataset_dir, args.stage_id)
    index_path = render_stage_index_html(dataset_dir)

    with_url = sum(1 for a in activities if a.get("activity_url"))
    print(f"stage={args.stage_id}")
    print(f"entries={len(entries)}")
    print(f"matched_now={matched}")
    print(f"updated_now={updated}")
    print(f"locked_skipped={locked_skipped}")
    print(f"distinct_riders_matched={len(seen_rider_ids)}")
    print(f"total_with_activity_url={with_url}")
    if args.download_gpx:
        print(f"gpx_new={gpx_new}")
        print(f"gpx_existing={gpx_existing}")
        print(f"gpx_fail={gpx_fail}")
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
