#!/usr/bin/env python3
"""Unified exporter for Strava and FlightAware tracks."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from lib.flightaware_to_csv import (
    extract_ident as fa_extract_ident,
    extract_track_points as fa_extract_track_points,
    fetch_html as fa_fetch_html,
    write_csv as fa_write_csv,
)
from lib.strava_to_gpx import (
    build_gpx as strava_build_gpx,
    create_web_session as strava_create_web_session,
    fetch_activity_web as strava_fetch_activity_web,
    fetch_streams_web as strava_fetch_streams_web,
    load_session_cookie_from_browser as strava_load_session_cookie_from_browser,
    parse_activity_id as strava_parse_activity_id,
)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
STRAVA_OUTPUT_DIR = OUTPUT_DIR / "courses"
FLIGHT_OUTPUT_DIR = OUTPUT_DIR / "flights"
DEFAULT_DATASET_DIR = BASE_DIR / "giro_2026"
DATASET_COURSES_DIR = DEFAULT_DATASET_DIR / "courses"
DATASET_FLIGHTS_DIR = DEFAULT_DATASET_DIR / "flights"


def now_iso_local() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid JSON object: {path}")
    return data


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
        f.write("\n")


def find_stage_date(dataset_dir: Path, stage_id: str) -> str | None:
    stages_path = dataset_dir / "stages.json"
    if not stages_path.exists():
        return None
    stages = load_json(stages_path).get("stages", [])
    if not isinstance(stages, list):
        return None
    for item in stages:
        if isinstance(item, dict) and item.get("stage_id") == stage_id:
            value = item.get("date")
            return value if isinstance(value, str) else None
    return None


def update_stage_link_record(
    dataset_dir: Path, stage_id: str, rider_id: str, activity_url: str, output_path: Path
) -> None:
    stage_date = find_stage_date(dataset_dir, stage_id)
    stage_file = dataset_dir / "stage_links" / f"{stage_id}.json"
    payload = load_json(stage_file) if stage_file.exists() else {}

    if not payload:
        payload = {
            "version": 1,
            "stage_id": stage_id,
            "date": stage_date,
            "statuses": ["found_public", "private_or_missing", "not_checked"],
            "activities": [],
        }

    activities = payload.get("activities")
    if not isinstance(activities, list):
        activities = []
        payload["activities"] = activities

    existing = None
    for row in activities:
        if isinstance(row, dict) and row.get("rider_id") == rider_id:
            existing = row
            break

    record = {
        "rider_id": rider_id,
        "status": "found_public",
        "activity_url": activity_url,
        "checked_at": now_iso_local(),
        "gpx_path": str(output_path),
    }
    if existing is None:
        activities.append(record)
    else:
        existing.update(record)

    save_json(stage_file, payload)


def update_stage_flight_record(
    dataset_dir: Path, stage_id: str, source_url: str, output_path: Path, callsign: str
) -> None:
    stages_path = dataset_dir / "stages.json"
    if not stages_path.exists():
        return

    payload = load_json(stages_path)
    stages = payload.get("stages", [])
    if not isinstance(stages, list):
        return

    target = None
    for item in stages:
        if isinstance(item, dict) and item.get("stage_id") == stage_id:
            target = item
            break
    if target is None:
        return

    flight = target.get("flight")
    if not isinstance(flight, dict):
        flight = {}
        target["flight"] = flight

    callsigns = flight.get("callsigns")
    if not isinstance(callsigns, list):
        callsigns = []
    if callsign and callsign not in callsigns:
        callsigns.append(callsign)
    flight["callsigns"] = callsigns

    urls = flight.get("source_urls")
    if not isinstance(urls, list):
        urls = []
    if source_url not in urls:
        urls.append(source_url)
    flight["source_urls"] = urls

    flight["track_status"] = "found"
    flight["track_csv_path"] = str(output_path)
    flight["checked_at"] = now_iso_local()

    save_json(stages_path, payload)


def cmd_strava(args: argparse.Namespace) -> int:
    activity_id = strava_parse_activity_id(args.activity)
    activity_url = (
        args.activity if str(args.activity).startswith("http") else f"https://www.strava.com/activities/{activity_id}"
    )
    if args.output:
        output_path = Path(args.output)
    elif args.stage_id and args.rider_id:
        output_path = DATASET_COURSES_DIR / args.stage_id / f"{args.rider_id}__activity_{activity_id}.gpx"
    else:
        output_path = STRAVA_OUTPUT_DIR / f"activity_{activity_id}.gpx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    session_cookie = args.session_cookie
    if not session_cookie:
        session_cookie = strava_load_session_cookie_from_browser(args.browser_cookie)

    web_session = strava_create_web_session(session_cookie)
    activity = strava_fetch_activity_web(web_session, activity_id, local_tz=args.local_tz)
    streams = strava_fetch_streams_web(web_session, activity_id)

    gpx = strava_build_gpx(activity, streams)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(gpx.to_xml())

    if args.stage_id and args.rider_id:
        update_stage_link_record(
            dataset_dir=Path(args.dataset_dir),
            stage_id=args.stage_id,
            rider_id=args.rider_id,
            activity_url=activity_url,
            output_path=output_path,
        )

    print(f"GPX created: {output_path}")
    print(f"Exported points: {len(gpx.tracks[0].segments[0].points)}")
    return 0


def cmd_flightaware(args: argparse.Namespace) -> int:
    html = fa_fetch_html(args.url)
    points = fa_extract_track_points(html)
    ident = fa_extract_ident(html)
    if args.output:
        output = Path(args.output)
    elif args.stage_id:
        output = DATASET_FLIGHTS_DIR / args.stage_id / f"{ident}_track.csv"
    else:
        output = FLIGHT_OUTPUT_DIR / f"{ident}_track.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    fa_write_csv(points, output)

    if args.stage_id:
        update_stage_flight_record(
            dataset_dir=Path(args.dataset_dir),
            stage_id=args.stage_id,
            source_url=args.url,
            output_path=output,
            callsign=ident,
        )

    print(f"CSV created: {output}")
    print(f"Points: {len(points)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified exporter: Strava activity to GPX and FlightAware track to CSV."
    )
    sub = parser.add_subparsers(dest="provider", required=True)

    strava = sub.add_parser("strava", help="Export Strava activity to GPX")
    strava.add_argument("activity", help="Strava activity URL or numeric id")
    strava.add_argument(
        "--session-cookie",
        default=None,
        help="Value of _strava4_session cookie (or set env STRAVA_SESSION_COOKIE).",
    )
    strava.add_argument(
        "--browser-cookie",
        choices=["auto", "chrome", "chromium", "firefox", "edge", "brave", "opera"],
        default="auto",
        help="Read _strava4_session directly from browser cookies (default: auto).",
    )
    strava.add_argument(
        "--local-tz",
        default=None,
        help="Timezone hint for Strava startDateLocal numeric field (example: Europe/Rome).",
    )
    strava.add_argument(
        "--stage-id",
        default=None,
        help="Stage identifier, e.g. S01. Enables per-stage output path and catalog update.",
    )
    strava.add_argument(
        "--rider-id",
        default=None,
        help="Rider identifier from riders.json, e.g. B001. Used with --stage-id.",
    )
    strava.add_argument(
        "--dataset-dir",
        default=str(DEFAULT_DATASET_DIR),
        help="Dataset folder containing stages.json and stage_links/ (default: giro_2026).",
    )
    strava.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output GPX path (default: activity_<id>.gpx)",
    )
    strava.set_defaults(func=cmd_strava)

    flightaware = sub.add_parser("flightaware", help="Export FlightAware history page to CSV")
    flightaware.add_argument("url", help="FlightAware history URL")
    flightaware.add_argument(
        "--stage-id",
        default=None,
        help="Stage identifier, e.g. S01. Enables per-stage output path and stages.json update.",
    )
    flightaware.add_argument(
        "--dataset-dir",
        default=str(DEFAULT_DATASET_DIR),
        help="Dataset folder containing stages.json (default: giro_2026).",
    )
    flightaware.add_argument("-o", "--output", default=None, help="Output CSV path")
    flightaware.set_defaults(func=cmd_flightaware)

    return parser


def run() -> int:
    parser = build_parser()
    argv = sys.argv[1:]
    providers = {"strava", "flightaware"}
    has_provider = any(token in providers for token in argv)
    if not has_provider:
        joined = " ".join(argv)
        if "flightaware.com" in joined:
            argv = ["flightaware", *argv]
        elif "strava.com/activities/" in joined:
            argv = ["strava", *argv]
    args = parser.parse_args(argv)

    if args.provider == "strava" and args.session_cookie is None:
        # Keep compatibility with existing env-based workflow.
        import os

        args.session_cookie = os.getenv("STRAVA_SESSION_COOKIE")

    if args.provider == "strava":
        if bool(args.stage_id) ^ bool(args.rider_id):
            raise RuntimeError("Use --stage-id and --rider-id together for catalog-aware Strava export.")

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(run())
