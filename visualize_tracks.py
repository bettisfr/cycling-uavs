#!/usr/bin/env python3
"""Build a Folium map with static paths and a fast custom time slider."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import folium
import gpxpy
from branca.element import Element
import import_stage_raw

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DATASET_DIR = BASE_DIR / "giro_2026"
COURSES_DIR = DATASET_DIR / "courses"
FLIGHTS_DIR = DATASET_DIR / "flights"

COLORS = [
    "#e41a1c",
    "#377eb8",
    "#4daf4a",
    "#984ea3",
    "#ff7f00",
    "#a65628",
    "#f781bf",
    "#999999",
]
FLIGHT_COLOR = "#8B5A2B"


def parse_iso_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def choose_indices(count: int, max_points: int) -> list[int]:
    if count <= max_points:
        return list(range(count))
    step = max(1, count // max_points)
    idx = list(range(0, count, step))
    if idx[-1] != count - 1:
        idx.append(count - 1)
    return idx


def load_gpx(path: Path, max_points: int) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        g = gpxpy.parse(f)
    points = g.tracks[0].segments[0].points if g.tracks and g.tracks[0].segments else []
    if not points:
        return []
    out: list[dict[str, Any]] = []
    for i in choose_indices(len(points), max_points):
        p = points[i]
        if p.time is None:
            continue
        out.append(
            {
                "t_ms": int(p.time.astimezone(timezone.utc).timestamp() * 1000),
                "lat": float(p.latitude),
                "lon": float(p.longitude),
                "alt_ft": int(round((p.elevation or 0.0) * 3.28084)) if p.elevation is not None else None,
                "speed_kt": float(p.speed * 1.94384) if p.speed is not None else None,
            }
        )
    return out


def select_time_coherent_tracks(candidates: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or len(candidates) <= count:
        return candidates
    starts = sorted(t["points"][0]["t_ms"] for t in candidates if t.get("points"))
    if not starts:
        return candidates[:count]
    median_start = starts[len(starts) // 2]
    ranked = sorted(
        candidates,
        key=lambda t: abs(t["points"][0]["t_ms"] - median_start) if t.get("points") else 10**18,
    )
    return ranked[:count]


def load_flight_csv(path: Path, max_points: int) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    for i in choose_indices(len(rows), max_points):
        row = rows[i]
        dt = parse_iso_utc(row.get("timestamp_utc", ""))
        if dt is None:
            continue
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        out.append(
            {
                "t_ms": int(dt.timestamp() * 1000),
                "lat": lat,
                "lon": lon,
                "alt_ft": int(float(row["alt_ft"])) if row.get("alt_ft") else None,
                "speed_kt": float(row["groundspeed_kt"]) if row.get("groundspeed_kt") else None,
            }
        )
    return out


def downsample_timeline(times_ms: list[int], max_steps: int) -> list[int]:
    times = sorted(set(times_ms))
    if not times:
        return []
    if len(times) <= max_steps:
        return times
    step = max(1, len(times) // max_steps)
    out = times[::step]
    if out[-1] != times[-1]:
        out.append(times[-1])
    return out


def default_flight_offset_for_stage(stage_id: str) -> float:
    try:
        n = int(stage_id[1:])
    except Exception:
        return 0.0
    return 60.0 if 1 <= n <= 3 else 0.0


def render_stage_map(args: argparse.Namespace, stage_id: str, flight_offset_min: float) -> int:
    courses_dir = Path(args.courses_dir) / stage_id
    flights_dir = Path(args.flights_dir) / stage_id
    out = DATASET_DIR / "html" / "maps" / f"{stage_id}.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    tracks: list[dict[str, Any]] = []
    color_idx = 0

    course_files = sorted(courses_dir.glob("*.gpx"))
    selected_files: list[Path] = []
    if args.bibs:
        wanted = {f"B{int(b):03d}" for b in args.bibs}
        by_bib: dict[str, list[Path]] = {}
        for gpx in course_files:
            stem = gpx.stem
            bib = stem.split("__", 1)[0]
            by_bib.setdefault(bib, []).append(gpx)
        for bib in sorted(wanted):
            files = sorted(by_bib.get(bib, []))
            if files:
                selected_files.append(files[0])
    else:
        selected_files = course_files

    course_candidates: list[dict[str, Any]] = []
    for gpx in selected_files:
        pts = load_gpx(gpx, args.max_points_per_track)
        if not pts:
            continue
        course_candidates.append({"name": gpx.stem, "kind": "course", "points": pts})

    if args.bibs:
        selected_courses = course_candidates
    elif args.all:
        selected_courses = course_candidates
    else:
        selected_courses = select_time_coherent_tracks(course_candidates, args.course_tracks)
    for c in selected_courses:
        tracks.append(
            {
                "name": c["name"],
                "kind": "course",
                "color": COLORS[color_idx % len(COLORS)],
                "idx": color_idx,
                "points": c["points"],
            }
        )
        color_idx += 1

    for csv_path in sorted(flights_dir.glob("*.csv")):
        pts = load_flight_csv(csv_path, args.max_points_per_track)
        if not pts:
            continue
        if flight_offset_min:
            delta_ms = int(flight_offset_min * 60_000)
            for p in pts:
                p["t_ms"] += delta_ms
        tracks.append({"name": csv_path.stem, "kind": "flight", "color": FLIGHT_COLOR, "idx": color_idx, "points": pts})
        color_idx += 1

    if not tracks:
        raise RuntimeError(f"No files found for stage {stage_id} in courses/flights.")

    center = (tracks[0]["points"][0]["lat"], tracks[0]["points"][0]["lon"])
    m = folium.Map(location=center, zoom_start=6, tiles="CartoDB positron")

    all_coords: list[tuple[float, float]] = []
    all_times: list[int] = []
    for t in tracks:
        coords = [(p["lat"], p["lon"]) for p in t["points"]]
        all_coords.extend(coords)
        all_times.extend([p["t_ms"] for p in t["points"]])
        folium.PolyLine(
            locations=coords,
            color=t["color"],
            weight=3 if t["kind"] == "flight" else 2,
            opacity=0.9 if t["kind"] == "flight" else 0.75,
            dash_array="8,6" if t["kind"] == "flight" else ("8,6" if t["kind"] == "course" and (t.get("idx", 0) % 2 == 1) else None),
            tooltip=t["name"],
        ).add_to(m)

    m.fit_bounds(all_coords)
    timeline = downsample_timeline(all_times, args.max_timeline_steps)

    js_tracks = [
        {
            "name": t["name"],
            "kind": t["kind"],
            "color": t["color"],
            "idx": t.get("idx", 0),
            "points": t["points"],
        }
        for t in tracks
    ]

    map_var = m.get_name()
    payload = json.dumps({"timeline": timeline, "tracks": js_tracks}, separators=(",", ":"))

    slider_html = f"""
<div id="time-control" style="
position: fixed; left: 20px; right: 20px; bottom: 20px; z-index: 9999;
background: rgba(255,255,255,0.96); border: 1px solid #ccc; border-radius: 8px;
padding: 10px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.18); font-family: sans-serif;">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
    <strong>Time Slider</strong>
    <span id="time-label">-</span>
  </div>
  <input id="time-slider" type="range" min="0" max="{max(0, len(timeline)-1)}" value="0" step="1" style="width:100%;margin-top:8px;">
</div>
"""
    slider_js = f"""
(function() {{
  const payload = {payload};
  const mapName = "{map_var}";
  const slider = document.getElementById('time-slider');
  const label = document.getElementById('time-label');

  function nearestPoint(points, targetMs) {{
    let lo = 0, hi = points.length - 1;
    if (targetMs <= points[0].t_ms) return points[0];
    if (targetMs >= points[hi].t_ms) return points[hi];
    while (lo <= hi) {{
      const mid = (lo + hi) >> 1;
      const t = points[mid].t_ms;
      if (t === targetMs) return points[mid];
      if (t < targetMs) lo = mid + 1; else hi = mid - 1;
    }}
    const a = points[Math.max(0, hi)];
    const b = points[Math.min(points.length - 1, lo)];
    return (Math.abs(a.t_ms - targetMs) <= Math.abs(b.t_ms - targetMs)) ? a : b;
  }}

  function popupHtml(track, p, targetMs) {{
    const dt = new Date(p.t_ms).toISOString().replace('T',' ').replace('.000Z',' UTC');
    const alt = (p.alt_ft == null) ? 'n/a' : (p.alt_ft + ' ft');
    const spd = (p.speed_kt == null) ? 'n/a' : (p.speed_kt.toFixed(1) + ' kt');
    const delta = Math.round(Math.abs(p.t_ms - targetMs) / 1000);
    return track.name + '<br>' + dt + '<br>Alt: ' + alt + '<br>Speed: ' + spd + '<br>Nearest: ' + delta + 's';
  }}

  function start(map) {{
    const markers = payload.tracks.map(t => {{
      const p = t.points[0];
      const r = (t.kind === 'flight') ? 6 : 5;
      const marker = L.circleMarker([p.lat, p.lon], {{
        radius: r, color: '#111', weight: 1, fillColor: t.color, fillOpacity: 0.92
      }}).addTo(map);
      marker.bindPopup(popupHtml(t, p, p.t_ms));
      return marker;
    }});

    function render(idx) {{
      const target = payload.timeline[idx];
      const targetIso = new Date(target).toISOString().replace('T',' ').replace('.000Z',' UTC');
      label.textContent = "Absolute time | " + targetIso;
      payload.tracks.forEach((t, i) => {{
        const localTarget = target;
        const p = nearestPoint(t.points, localTarget);
        let lat = p.lat;
        let lon = p.lon;
        if (i > 0) {{
          const bump = 0.00035;
          const ang = (i + 1) * 1.5708;
          lat = lat + Math.cos(ang) * bump;
          lon = lon + Math.sin(ang) * bump;
        }}
        markers[i].setLatLng([lat, lon]);
        markers[i].setPopupContent(popupHtml(t, p, localTarget));
      }});
    }}

    slider.addEventListener('input', () => render(Number(slider.value)));
    render(0);
  }}

  function waitMap() {{
    const map = window[mapName];
    if (!map) {{
      setTimeout(waitMap, 50);
      return;
    }}
    start(map);
  }}

  waitMap();
}})();
"""
    m.get_root().html.add_child(Element(slider_html))
    m.get_root().script.add_child(Element(slider_js))
    m.save(str(out))
    print(
        f"[OK] {stage_id} | tracks={len(tracks)} | "
        f"offset_min={flight_offset_min:g} | map={out}"
    )
    return len(tracks)


def run() -> int:
    parser = argparse.ArgumentParser(description="Create Folium map with static tracks + single nearest markers.")
    parser.add_argument("--stage-id", default=None, help="Stage id (e.g. S01). Uses giro_2026/courses/<stage> and flights/<stage>.")
    parser.add_argument("--courses-dir", default=str(COURSES_DIR))
    parser.add_argument("--flights-dir", default=str(FLIGHTS_DIR))
    parser.add_argument("-o", "--output", default=None, help="Output HTML path. Default: giro_2026/html/maps/<stage>.html when --stage-id is set.")
    parser.add_argument("--max-points-per-track", type=int, default=600)
    parser.add_argument("--max-timeline-steps", type=int, default=1800)
    parser.add_argument("--course-tracks", type=int, default=5, help="Number of rider GPX tracks to load.")
    parser.add_argument("--bibs", nargs="*", type=int, default=None, help="Explicit rider bib list (e.g. --bibs 6 131 192).")
    parser.add_argument("--flight-offset-min", type=float, default=60.0, help="Offset (minutes) applied to flight timestamps.")
    parser.add_argument("--all", action="store_true", help="Generate maps for all stages in giro_2026/stages.json.")
    args = parser.parse_args()

    if args.all:
        stages_path = DATASET_DIR / "stages.json"
        stages = json.loads(stages_path.read_text(encoding="utf-8")).get("stages", [])
        stage_ids = [s.get("stage_id") for s in stages if s.get("stage_id")]
        if not stage_ids:
            raise RuntimeError("No stages found in giro_2026/stages.json")
        print(f"Generating maps for {len(stage_ids)} stages...")
        total_tracks = 0
        ok = 0
        fail = 0
        for sid in stage_ids:
            auto_offset = default_flight_offset_for_stage(str(sid))
            try:
                n = render_stage_map(args, str(sid), auto_offset)
                total_tracks += n
                ok += 1
            except Exception as exc:
                fail += 1
                print(f"[FAIL] {sid} | {exc}")
        print()
        print("=== Map Summary ===")
        print(f"stages_total: {len(stage_ids)}")
        print(f"stages_ok: {ok}")
        print(f"stages_fail: {fail}")
        print(f"tracks_loaded_total: {total_tracks}")
    else:
        if not args.stage_id:
            raise RuntimeError("Provide --stage-id or use --all.")
        n = render_stage_map(args, args.stage_id, args.flight_offset_min)
        print("=== Map Summary ===")
        print("stages_total: 1")
        print("stages_ok: 1")
        print("stages_fail: 0")
        print(f"tracks_loaded_total: {n}")

    try:
        import_stage_raw.render_stage_index_html(DATASET_DIR)
    except Exception:
        # Keep map generation successful even if index refresh fails.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
