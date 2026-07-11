#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

import gpxpy
import matplotlib.pyplot as plt
from PIL import Image
from pipeline.competition import load_competition


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def pick_first_gpx(stage_dir: Path) -> Path:
    files = sorted(stage_dir.glob("B*__activity_*.gpx"))
    if not files:
        raise RuntimeError(f"No GPX found in {stage_dir}")
    return files[0]


def gpx_distance_km(gpx_path: Path) -> float:
    with gpx_path.open("r", encoding="utf-8", errors="ignore") as f:
        g = gpxpy.parse(f)
    pts: list[tuple[float, float]] = []
    for tr in g.tracks:
        for seg in tr.segments:
            for p in seg.points:
                pts.append((p.latitude, p.longitude))
    if len(pts) < 2:
        return 0.0
    dist_m = 0.0
    for i in range(1, len(pts)):
        a = pts[i - 1]
        b = pts[i]
        dist_m += haversine_m(a[0], a[1], b[0], b[1])
    return dist_m / 1000.0


def pick_default_gpx(stage_dir: Path) -> Path:
    files = sorted(stage_dir.glob("B*__activity_*.gpx"))
    if not files:
        raise RuntimeError(f"No GPX found in {stage_dir}")
    # Fast default: use B002 when available, fallback to first GPX.
    for p in files:
        if p.name.startswith("B002__activity_"):
            return p
    return files[0]


def latlon_to_tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2**z
    xtile = (lon + 180.0) / 360.0 * n
    ytile = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return xtile, ytile


def choose_zoom(lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> int:
    # Heuristic for stage-size extents.
    span = max(lat_max - lat_min, lon_max - lon_min)
    if span > 4.0:
        return 7
    if span > 2.0:
        return 8
    if span > 1.0:
        return 9
    if span > 0.5:
        return 10
    return 11


def build_osm_background(lats: list[float], lons: list[float]) -> tuple[Image.Image, int, int, int, int, int]:
    pad = 0.08
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_span = max(lat_max - lat_min, 1e-4)
    lon_span = max(lon_max - lon_min, 1e-4)
    lat_min -= lat_span * pad
    lat_max += lat_span * pad
    lon_min -= lon_span * pad
    lon_max += lon_span * pad
    z = choose_zoom(lat_min, lat_max, lon_min, lon_max)

    x0f, y1f = latlon_to_tile(lat_min, lon_min, z)
    x1f, y0f = latlon_to_tile(lat_max, lon_max, z)
    x0, x1 = int(math.floor(min(x0f, x1f))), int(math.floor(max(x0f, x1f)))
    y0, y1 = int(math.floor(min(y0f, y1f))), int(math.floor(max(y0f, y1f)))

    w_tiles = x1 - x0 + 1
    h_tiles = y1 - y0 + 1
    canvas = Image.new("RGB", (w_tiles * 256, h_tiles * 256), (240, 240, 240))
    headers = {"User-Agent": "cycling-uavs/1.0"}

    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            url = f"https://tile.openstreetmap.org/{z}/{tx}/{ty}.png"
            req = Request(url, headers=headers)
            with urlopen(req, timeout=20) as r:
                tile = Image.open(BytesIO(r.read())).convert("RGB")
            canvas.paste(tile, ((tx - x0) * 256, (ty - y0) * 256))
    return canvas, z, x0, y0, x1, y1


def load_points(gpx_path: Path) -> list[tuple[float, float, float | None]]:
    with gpx_path.open("r", encoding="utf-8", errors="ignore") as f:
        g = gpxpy.parse(f)
    pts: list[tuple[float, float, float | None]] = []
    for tr in g.tracks:
        for seg in tr.segments:
            for p in seg.points:
                pts.append((p.latitude, p.longitude, p.elevation))
    if len(pts) < 2:
        raise SystemExit(f"Not enough points in {gpx_path}")
    return pts


def build_profiles(pts: list[tuple[float, float, float | None]]) -> tuple[list[float], list[float]]:
    d_km = [0.0]
    ele = [float(pts[0][2] or 0.0)]
    for i in range(1, len(pts)):
        a = pts[i - 1]
        b = pts[i]
        d = haversine_m(a[0], a[1], b[0], b[1]) / 1000.0
        d_km.append(d_km[-1] + d)
        ele.append(float(b[2] or ele[-1]))
    return d_km, ele


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate stage planimetry/elevation PNG images from first GPX.")
    ap.add_argument("--stage-id", default=None, help="e.g. S06")
    ap.add_argument("--all", action="store_true", help="Generate images for all SXX folders in courses/")
    ap.add_argument("--competition-dir", required=True)
    args = ap.parse_args()
    if not args.all and not args.stage_id:
        ap.error("either --stage-id or --all is required")

    comp = load_competition(args.competition_dir)
    base = comp.root
    if args.all:
        stage_ids = sorted(p.name for p in (base / "courses").glob("S*") if p.is_dir())
    else:
        stage_ids = [str(args.stage_id)]

    for stage_id in stage_ids:
        gpx_dir = base / "courses" / stage_id
        try:
            gpx_path = pick_default_gpx(gpx_dir)
            pts = load_points(gpx_path)
        except Exception as exc:
            print(f"[SKIP] {stage_id} {exc}")
            continue
        d_km, ele = build_profiles(pts)

        out_dir = base / "html" / "stages" / "assets" / stage_id
        out_dir.mkdir(parents=True, exist_ok=True)
        plan_path = out_dir / "planimetry.png"
        elev_path = out_dir / "elevation.png"

        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]

        try:
            bg, z, x0, y0, _, _ = build_osm_background(lats, lons)
            fig, ax = plt.subplots(figsize=(8, 6), dpi=150)
            ax.imshow(bg, extent=[0, bg.size[0], bg.size[1], 0])
            px = []
            py = []
            for lat, lon in zip(lats, lons):
                xt, yt = latlon_to_tile(lat, lon, z)
                px.append((xt - x0) * 256)
                py.append((yt - y0) * 256)
            ax.plot(px, py, color="#1f5fbf", linewidth=2.0)
            ax.scatter([px[0]], [py[0]], c="#2ca02c", s=30, label="start", zorder=3)
            ax.scatter([px[-1]], [py[-1]], c="#d62728", s=30, label="finish", zorder=3)
            ax.set_title(f"{stage_id} Planimetry (OSM)")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.legend(loc="best")
            fig.tight_layout()
            fig.savefig(plan_path)
            plt.close(fig)
        except Exception:
            # Fallback without basemap.
            plt.figure(figsize=(8, 6), dpi=150)
            plt.plot(lons, lats, color="#1f5fbf", linewidth=1.6)
            plt.scatter([lons[0]], [lats[0]], c="#2ca02c", s=25, label="start")
            plt.scatter([lons[-1]], [lats[-1]], c="#d62728", s=25, label="finish")
            plt.title(f"{stage_id} Planimetry")
            plt.xlabel("Longitude")
            plt.ylabel("Latitude")
            plt.legend(loc="best")
            plt.tight_layout()
            plt.savefig(plan_path)
            plt.close()

        plt.figure(figsize=(10, 3.5), dpi=150)
        plt.plot(d_km, ele, color="#d97706", linewidth=1.6)
        plt.fill_between(d_km, ele, min(ele), color="#f7c97b", alpha=0.55)
        plt.title(f"{stage_id} Elevation Profile")
        plt.xlabel("Distance (km)")
        plt.ylabel("Elevation (m)")
        plt.tight_layout()
        plt.savefig(elev_path)
        plt.close()

        rider = re.match(r"(B\d+)", gpx_path.name)
        print(f"[OK] {stage_id} rider={rider.group(1) if rider else 'unknown'}")
        print(f"     planimetry={plan_path}")
        print(f"     elevation={elev_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
