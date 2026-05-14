#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def extract_start_hhmm(gpx_path: Path) -> str:
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
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw.endswith("Z") else datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone().strftime("%H:%M")
    except Exception:
        pass
    return "-"


def rider_strava_enabled(rider: dict) -> bool:
    if rider.get("enabled", True) is False:
        return False
    if rider.get("strava_enabled", True) is False:
        return False
    strava_cfg = rider.get("strava")
    if isinstance(strava_cfg, dict) and strava_cfg.get("enabled", True) is False:
        return False
    return True


def generate_rider_page(base: Path, rider: dict, stages: list[dict], stage_links: dict[str, dict]) -> Path:
    rows: list[str] = []
    for s in stages:
        sid = s["stage_id"]
        route = f"{s.get('start_city', '')} \u2192 {s.get('finish_city', '')}"
        activity_url = ""
        payload = stage_links.get(sid, {})
        for a in payload.get("activities", []):
            if a.get("rider_id") == rider["rider_id"]:
                activity_url = a.get("activity_url") or ""
                break

        gpx_cell = "-"
        start = "-"
        if activity_url:
            m = re.search(r"/activities/(\d+)", activity_url)
            if m:
                aid = m.group(1)
                gpx = base / "courses" / sid / f"{rider['rider_id']}__activity_{aid}.gpx"
                if gpx.exists() and gpx.stat().st_size > 0:
                    gpx_cell = f'<a href="file://{gpx}" target="_blank" rel="noopener noreferrer">yes</a>'
                    start = extract_start_hhmm(gpx)
        activity_cell = (
            f'<a href="{activity_url}" target="_blank" rel="noopener noreferrer">activity</a>'
            if activity_url
            else "-"
        )
        rows.append(
            f"<tr><td>{sid}</td><td>{s.get('date', '')}</td><td>{route}</td><td>{activity_cell}</td><td>{gpx_cell}</td><td>{start}</td></tr>"
        )

    profile = rider.get("strava_athlete_url") if rider_strava_enabled(rider) else ""
    profile_cell = (
        f'<a href="{profile}" target="_blank" rel="noopener noreferrer">profile</a>' if profile else "-"
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{rider['rider_id']} - {rider['name']}</title>
  <link rel="stylesheet" href="../style.css" />
</head>
<body>
  <div class="wrap">
    <h1>{rider['rider_id']} - {rider['name']}</h1>
    <div class="meta-grid">
      <div class="meta-card"><b>Bib:</b> {rider.get('bib', '')}</div>
      <div class="meta-card"><b>Team:</b> {rider.get('team_name', '')}</div>
      <div class="meta-card"><b>Nationality:</b> {rider.get('nationality', '')}</div>
      <div class="meta-card"><b>Strava profile:</b> {profile_cell}</div>
    </div>
    <div class="toolbar"><a href="../index.html">Back to stage index</a></div>
    <div class="tbl"><table>
      <thead>
        <tr><th>Stage</th><th>Date</th><th>Route</th><th>Stage Activity</th><th>GPX</th><th>Start</th></tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table></div>
  </div>
</body>
</html>
"""

    out_dir = base / "html" / "riders"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{rider['rider_id']}.html"
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate rider HTML summaries across all stages.")
    ap.add_argument("--rider-id", help="Rider id, e.g. B006")
    ap.add_argument("--all", action="store_true", help="Generate pages for all riders")
    ap.add_argument("--dataset-dir", default="giro_2026")
    args = ap.parse_args()

    if not args.all and not args.rider_id:
        ap.error("use --rider-id BXXX or --all")
    if args.all and args.rider_id:
        ap.error("use either --all or --rider-id")

    base = Path(args.dataset_dir)
    riders = json.loads((base / "riders.json").read_text(encoding="utf-8"))["riders"]
    stages = json.loads((base / "stages.json").read_text(encoding="utf-8"))["stages"]
    stage_links: dict[str, dict] = {}
    for s in stages:
        sid = s["stage_id"]
        p = base / "stage_links" / f"{sid}.json"
        if p.exists():
            stage_links[sid] = json.loads(p.read_text(encoding="utf-8"))
    if args.all:
        count = 0
        for rider in riders:
            out = generate_rider_page(base, rider, stages, stage_links)
            print(out)
            count += 1
        print(f"generated={count}")
        return 0

    rider = next((r for r in riders if r.get("rider_id") == args.rider_id), None)
    if rider is None:
        raise SystemExit(f"Rider not found: {args.rider_id}")
    out = generate_rider_page(base, rider, stages, stage_links)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
