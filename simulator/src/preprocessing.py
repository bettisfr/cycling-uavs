"""Build a normalized rider-point table for one Giro 2026 stage."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def resolve_gpx_path(path_text: str, repo: Path, competition_dir: Path) -> Path:
    path = Path(path_text)
    candidates: list[Path]
    if path.is_absolute():
        candidates = [path]
        # Some historical metadata was produced under an equivalent mount point.
        text = str(path)
        if text.startswith("/mnt/data/github/"):
            candidates.append(Path("/home/fra/Desktop/github") / text.removeprefix("/mnt/data/github/"))
        elif text.startswith("/home/fra/Desktop/github/"):
            candidates.append(Path("/mnt/data/github") / text.removeprefix("/home/fra/Desktop/github/"))
    else:
        candidates = [repo / path, competition_dir / path, competition_dir / path.name]

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0]


def iter_gpx_points(gpx_path: Path):
    for _event, elem in ET.iterparse(gpx_path, events=("end",)):
        if local_name(elem.tag) != "trkpt":
            continue

        lat = elem.attrib.get("lat")
        lon = elem.attrib.get("lon")
        ele = ""
        time = ""
        for child in elem:
            name = local_name(child.tag)
            if name == "ele":
                ele = child.text or ""
            elif name == "time":
                time = child.text or ""

        if lat and lon and time:
            yield time, lat, lon, ele
        elem.clear()


def load_stage_activities(competition_dir: Path, stage_id: str) -> list[dict]:
    stage_path = competition_dir / "stage_links" / f"{stage_id}.json"
    if not stage_path.exists():
        raise FileNotFoundError(f"Missing stage link file: {stage_path}")
    with stage_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("activities", [])


def select_activities(
    activities: list[dict],
    repo: Path,
    competition_dir: Path,
    include_unlocked: bool,
    include_midnight: bool,
    min_start_hhmm: str,
) -> tuple[list[tuple[dict, Path]], dict[str, int]]:
    selected: list[tuple[dict, Path]] = []
    excluded: dict[str, int] = {
        "not_found_public": 0,
        "unlocked": 0,
        "midnight_start": 0,
        "early_start": 0,
        "missing_gpx_path": 0,
        "missing_gpx_file": 0,
    }

    for activity in activities:
        if activity.get("status") != "found_public":
            excluded["not_found_public"] += 1
            continue
        if not include_unlocked and not activity.get("locked"):
            excluded["unlocked"] += 1
            continue
        if not include_midnight and activity.get("gpx_start_hhmm") == "00:00":
            excluded["midnight_start"] += 1
            continue
        if activity.get("gpx_start_hhmm", "00:00") < min_start_hhmm:
            excluded["early_start"] += 1
            continue

        gpx_path_text = activity.get("gpx_path")
        if not gpx_path_text:
            excluded["missing_gpx_path"] += 1
            continue

        gpx_path = resolve_gpx_path(gpx_path_text, repo, competition_dir)
        if not gpx_path.exists():
            excluded["missing_gpx_file"] += 1
            continue

        selected.append((activity, gpx_path))

    return selected, excluded


def write_csv(rows: dict[str, list], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows)
    n = len(next(iter(rows.values()))) if rows else 0
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(fieldnames)
        for i in range(n):
            writer.writerow([rows[name][i] for name in fieldnames])


def write_parquet(rows: dict[str, list], output_path: Path) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pyarrow is required to write Parquet. Install dependencies with "
            "`/home/fra/pyvenv/bin/pip install -r requirements.txt`."
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(rows)
    pq.write_table(table, output_path, compression="zstd")


def build_stage_trace(args: argparse.Namespace) -> dict:
    repo = repo_root()
    competition_dir = Path(args.competition_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = repo / output_dir

    stage_id = args.stage_id.upper()
    activities = load_stage_activities(competition_dir, stage_id)
    selected, excluded = select_activities(
        activities,
        repo,
        competition_dir,
        include_unlocked=args.include_unlocked,
        include_midnight=args.include_midnight,
        min_start_hhmm=args.min_start_hhmm,
    )

    columns: dict[str, list] = {
        "stage_id": [],
        "rider_id": [],
        "time_utc": [],
        "lat": [],
        "lon": [],
        "ele_m": [],
        "source_gpx": [],
        "activity_url": [],
        "gpx_start_hhmm": [],
    }
    per_rider_points: dict[str, int] = {}

    for activity, gpx_path in selected:
        rider_id = activity.get("rider_id", "")
        count = 0
        for time_utc, lat, lon, ele_m in iter_gpx_points(gpx_path):
            columns["stage_id"].append(stage_id)
            columns["rider_id"].append(rider_id)
            columns["time_utc"].append(time_utc)
            columns["lat"].append(float(lat))
            columns["lon"].append(float(lon))
            columns["ele_m"].append(float(ele_m) if ele_m else None)
            columns["source_gpx"].append(str(gpx_path))
            columns["activity_url"].append(activity.get("activity_url", ""))
            columns["gpx_start_hhmm"].append(activity.get("gpx_start_hhmm", ""))
            count += 1
        per_rider_points[rider_id] = count

    csv_path = output_dir / f"{stage_id}_rider_points.csv"
    parquet_path = output_dir / f"{stage_id}_rider_points.parquet"
    summary_path = output_dir / f"{stage_id}_summary.json"

    write_csv(columns, csv_path)
    write_parquet(columns, parquet_path)

    n_points = len(columns["stage_id"])
    times = columns["time_utc"]
    summary = {
        "stage_id": stage_id,
        "competition_dir": str(competition_dir),
        "csv_path": str(csv_path),
        "parquet_path": str(parquet_path),
        "summary_path": str(summary_path),
        "riders_included": len(selected),
        "points": n_points,
        "time_min_utc": min(times) if times else None,
        "time_max_utc": max(times) if times else None,
        "excluded": excluded,
        "min_start_hhmm": args.min_start_hhmm,
        "per_rider_points_min": min(per_rider_points.values()) if per_rider_points else 0,
        "per_rider_points_max": max(per_rider_points.values()) if per_rider_points else 0,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary

