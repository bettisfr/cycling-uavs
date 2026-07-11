"""Render a Folium map for an experiment solution with a time slider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from branca.element import Element
import folium

from experiments.algorithms.milp import greedy_clusters, read_bucketed_rider_positions


UAV_COLORS = [
    "#1f77b4",
    "#d62728",
    "#2ca02c",
    "#9467bd",
    "#ff7f0e",
    "#17becf",
    "#8c564b",
    "#e377c2",
]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        return path.resolve()

    text = str(path)
    candidates = [path]
    if text.startswith("/mnt/data/github/"):
        candidates.append(
            Path("/home/fra/Desktop/github") / text.removeprefix("/mnt/data/github/")
        )
    elif text.startswith("/home/fra/Desktop/github/"):
        candidates.append(Path("/mnt/data/github") / text.removeprefix("/home/fra/Desktop/github/"))

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return path


def choose_indices(count: int, max_points: int) -> list[int]:
    if count <= max_points:
        return list(range(count))
    step = max(1, count // max_points)
    indices = list(range(0, count, step))
    if indices[-1] != count - 1:
        indices.append(count - 1)
    return indices


def load_gpx_coords(path_text: str | None, max_points: int) -> list[tuple[float, float]]:
    if not path_text:
        return []

    path = resolve_path(path_text)
    if not path.exists():
        return []

    points: list[tuple[float, float]] = []
    for _event, elem in ET.iterparse(path, events=("end",)):
        if local_name(elem.tag) == "trkpt":
            lat = elem.attrib.get("lat")
            lon = elem.attrib.get("lon")
            if lat and lon:
                points.append((float(lat), float(lon)))
        elem.clear()

    return [points[i] for i in choose_indices(len(points), max_points)]


def build_full_stage_layers(
    trace_parquet: Path,
    time_step_sec: int,
    min_riders_per_bucket: int,
    cluster_radius_m: float,
) -> tuple[list[int], list[list[dict]], list[list[dict]]]:
    by_bucket = read_bucketed_rider_positions(trace_parquet, time_step_sec)
    buckets = [
        bucket
        for bucket in sorted(by_bucket)
        if len(by_bucket[bucket]) >= min_riders_per_bucket
    ]

    all_riders: list[list[dict]] = []
    all_clusters: list[list[dict]] = []
    for bucket in buckets:
        rider_points = [
            {
                "bucket": bucket,
                "rider_id": rider_id,
                "lat": point.lat,
                "lon": point.lon,
            }
            for rider_id, point in sorted(by_bucket[bucket].items())
        ]
        clusters = greedy_clusters(
            list(by_bucket[bucket].values()),
            cluster_radius_m,
        )
        cluster_points = [
            {
                "bucket": bucket,
                "cluster": cluster_idx,
                "lat": cluster.lat,
                "lon": cluster.lon,
                "weight": cluster.weight,
            }
            for cluster_idx, cluster in enumerate(clusters)
        ]
        all_riders.append(rider_points)
        all_clusters.append(cluster_points)

    return buckets, all_riders, all_clusters


def render_map(args: argparse.Namespace) -> Path:
    data = json.loads(args.solution_json.read_text(encoding="utf-8"))
    algorithm_name = data.get("algorithm_name") or data.get("status_name") or "experiment"
    solution_title = f"{str(algorithm_name).upper()} solution"
    stations = data.get("stations", [])
    placements = data.get("placements", [])
    buckets = data.get("time_buckets", [])
    clusters = data.get("clusters", [])
    rider_points = data.get("rider_points", [])
    if args.full_stage_trace:
        full_buckets, full_rider_points, full_clusters = build_full_stage_layers(
            args.full_stage_trace,
            int(data.get("time_step_sec") or args.time_step_sec),
            args.min_riders_per_bucket,
            args.cluster_radius_m,
        )
        layer_by_bucket = {
            bucket: (points, bucket_clusters)
            for bucket, points, bucket_clusters in zip(
                full_buckets,
                full_rider_points,
                full_clusters,
                strict=True,
            )
        }
        buckets = data.get("time_buckets", full_buckets)
        rider_points = [layer_by_bucket.get(bucket, ([], []))[0] for bucket in buckets]
        clusters = [layer_by_bucket.get(bucket, ([], []))[1] for bucket in buckets]
    route = load_gpx_coords(
        data.get("station_metadata", {}).get("reference_gpx"),
        args.max_route_points,
    )

    coords: list[tuple[float, float]] = []
    coords.extend(route)
    coords.extend((s["lat"], s["lon"]) for s in stations)
    coords.extend((p["lat"], p["lon"]) for p in placements)
    if not coords:
        raise RuntimeError(f"No coordinates found in {args.solution_json}")

    cluster_coords = [
        (cluster["lat"], cluster["lon"])
        for bucket_clusters in clusters
        for cluster in bucket_clusters
    ]
    rider_coords = [
        (point["lat"], point["lon"])
        for bucket_points in rider_points
        for point in bucket_points
    ]
    solution_coords = [(p["lat"], p["lon"]) for p in placements] + cluster_coords + rider_coords
    center_coords = solution_coords or coords
    center = center_coords[len(center_coords) // 2]
    m = folium.Map(location=center, zoom_start=10, tiles="CartoDB positron")

    if route:
        folium.PolyLine(
            route,
            color="#555555",
            weight=3,
            opacity=0.65,
            tooltip="Reference GPX route",
        ).add_to(m)

    for station in stations:
        folium.CircleMarker(
            location=(station["lat"], station["lon"]),
            radius=5,
            color="#111111",
            weight=1,
            fill=True,
            fill_color="#f2c94c",
            fill_opacity=0.95,
            tooltip=station["label"],
        ).add_to(m)

    m.fit_bounds(solution_coords or coords)

    payload = {
        "buckets": buckets,
        "time_step_sec": data.get("time_step_sec"),
        "coverage_radius_m": data.get("coverage_radius_m", 250.0),
        "num_uavs": data.get("num_uavs"),
        "objective": data.get("objective"),
        "total_cluster_weight": data.get("total_cluster_weight"),
        "solution_buckets": data.get("time_buckets", []),
        "placements": placements,
        "clusters": clusters,
        "rider_points": rider_points,
        "colors": UAV_COLORS,
    }
    map_var = m.get_name()
    encoded = json.dumps(payload, separators=(",", ":"))

    slider_html = f"""
<div id="solution-control" style="
position: fixed; left: 20px; right: 20px; bottom: 20px; z-index: 9999;
background: rgba(255,255,255,0.96); border: 1px solid #c9c9c9; border-radius: 8px;
padding: 10px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.18); font-family: sans-serif;">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
    <strong>{solution_title}</strong>
    <span id="solution-label">-</span>
  </div>
  <input id="solution-slider" type="range" min="0" max="{max(0, len(buckets) - 1)}" value="0" step="1" style="width:100%;margin-top:8px;">
  <div id="solution-stats" style="font-size:12px;color:#444;margin-top:6px;"></div>
  <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:12px;color:#333;margin-top:6px;">
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#1f77b4;"></span> UAV</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#f2c94c;border:1px solid #111;"></span> station</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#555;"></span> rider</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#2ca02c;"></span> covered cluster</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#d62728;"></span> uncovered cluster</span>
    <span>transparent circle = coverage radius</span>
  </div>
</div>
"""

    slider_js = f"""
window.addEventListener('load', function() {{
  const payload = {encoded};
  const mapObj = window["{map_var}"];
  const slider = document.getElementById('solution-slider');
  const label = document.getElementById('solution-label');
  const stats = document.getElementById('solution-stats');
  const markers = [];
  const clusterLayers = [];
  const riderLayers = [];
  const coverageLayers = [];
  const byBucket = new Map();
  const clustersByBucket = new Map();
  const ridersByBucket = new Map();

  if (!mapObj || !slider || !label || !stats) {{
    console.error('Solution map initialization failed.');
    return;
  }}

  for (const p of payload.placements) {{
    if (!byBucket.has(p.bucket)) byBucket.set(p.bucket, []);
    byBucket.get(p.bucket).push(p);
  }}

  for (const bucketClusters of payload.clusters || []) {{
    for (const c of bucketClusters) {{
      if (!clustersByBucket.has(c.bucket)) clustersByBucket.set(c.bucket, []);
      clustersByBucket.get(c.bucket).push(c);
    }}
  }}

  for (const bucketRiders of payload.rider_points || []) {{
    for (const r of bucketRiders) {{
      if (!ridersByBucket.has(r.bucket)) ridersByBucket.set(r.bucket, []);
      ridersByBucket.get(r.bucket).push(r);
    }}
  }}

  function distanceM(a, b) {{
    const R = 6371000;
    const toRad = Math.PI / 180;
    const lat1 = a.lat * toRad;
    const lat2 = b.lat * toRad;
    const dlat = lat2 - lat1;
    const dlon = (b.lon - a.lon) * toRad;
    const x = dlon * Math.cos((lat1 + lat2) / 2);
    return R * Math.sqrt(x * x + dlat * dlat);
  }}

  function clearLayerList(layers) {{
    while (layers.length) {{
      const layer = layers.pop();
      mapObj.removeLayer(layer);
    }}
  }}

  function markerHtml(p) {{
    const color = payload.colors[p.uav % payload.colors.length];
    const text = String(p.uav);
    return `<div style="
      width:24px;height:24px;border-radius:50%;
      background:${{color}};color:white;border:2px solid white;
      box-shadow:0 1px 5px rgba(0,0,0,0.45);
      display:flex;align-items:center;justify-content:center;
      font:700 12px sans-serif;">${{text}}</div>`;
  }}

  function clearMarkers() {{
    clearLayerList(markers);
    clearLayerList(riderLayers);
    clearLayerList(clusterLayers);
    clearLayerList(coverageLayers);
  }}

  function draw(index) {{
    clearMarkers();
    const bucket = payload.buckets[index];
    const points = (byBucket.get(bucket) || []).slice().sort((a, b) => a.uav - b.uav);
    const riders = (ridersByBucket.get(bucket) || []).slice();
    const clusters = (clustersByBucket.get(bucket) || []).slice().sort((a, b) => b.weight - a.weight);
    const hasSolution = points.length > 0;

    let coveredWeight = 0;
    let totalWeight = 0;
    for (const c of clusters) {{
      const covered = points.some((p) => distanceM(p, c) <= payload.coverage_radius_m);
      totalWeight += c.weight;
      if (covered) coveredWeight += c.weight;
      const color = hasSolution ? (covered ? '#2ca02c' : '#d62728') : '#777777';
      const stroke = hasSolution ? (covered ? '#1f7a3a' : '#9f1d20') : '#555555';
      const layer = L.circleMarker([c.lat, c.lon], {{
        radius: Math.max(5, Math.min(18, 4 + Math.sqrt(c.weight) * 1.8)),
        color: stroke,
        weight: 2,
        fill: true,
        fillColor: color,
        fillOpacity: 0.18,
        opacity: 0.75
      }}).addTo(mapObj);
      layer.bindTooltip(
        `cluster ${{c.cluster}}<br>riders: ${{c.weight}}<br>${{hasSolution ? (covered ? 'covered' : 'uncovered') : 'no solution in this slot'}}`,
        {{sticky: true}}
      );
      clusterLayers.push(layer);
    }}

    for (const r of riders) {{
      const layer = L.circleMarker([r.lat, r.lon], {{
        radius: 3.5,
        color: '#ffffff',
        weight: 1,
        fill: true,
        fillColor: '#222222',
        fillOpacity: 0.9,
        opacity: 1.0
      }}).addTo(mapObj);
      layer.bindTooltip(`rider ${{r.rider_id}}`, {{sticky: true}});
      riderLayers.push(layer);
    }}

    for (const p of points) {{
      const coverage = L.circle([p.lat, p.lon], {{
        radius: payload.coverage_radius_m,
        color: payload.colors[p.uav % payload.colors.length],
        weight: 1,
        fill: true,
        fillOpacity: 0.07,
        opacity: 0.35
      }}).addTo(mapObj);
      coverageLayers.push(coverage);

      const marker = L.marker([p.lat, p.lon], {{
        icon: L.divIcon({{
          className: 'uav-solution-marker',
          html: markerHtml(p),
          iconSize: [24, 24],
          iconAnchor: [12, 12]
        }})
      }}).addTo(mapObj);
      marker.bindTooltip(
        `UAV ${{p.uav}}<br>${{p.kind}}: ${{p.label}}<br>battery: ${{Number(p.battery).toFixed(1)}}`,
        {{sticky: true}}
      );
      markers.push(marker);
    }}

    const elapsed = index * payload.time_step_sec;
    label.textContent = `slot ${{index + 1}}/${{payload.buckets.length}} | bucket ${{bucket}} | +${{elapsed}}s`;
    stats.textContent = hasSolution
      ? `UAVs: ${{payload.num_uavs}} | riders shown: ${{riders.length}} | slot coverage: ${{coveredWeight}} / ${{totalWeight}} riders | objective: ${{payload.objective}} / ${{payload.total_cluster_weight}}`
      : `riders shown: ${{riders.length}} | clusters: ${{clusters.length}} | no solution in this slot`;
  }}

  slider.addEventListener('input', () => draw(Number(slider.value)));
  draw(0);
}});
"""

    m.get_root().html.add_child(Element(slider_html))
    m.get_root().script.add_child(Element(slider_js))

    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(args.output_html))
    return args.output_html
