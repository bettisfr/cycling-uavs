#!/usr/bin/env python3
"""Unified exporter for Strava and FlightAware tracks."""

from __future__ import annotations

import argparse
from pathlib import Path

from flightaware_to_csv import (
    extract_ident as fa_extract_ident,
    extract_track_points as fa_extract_track_points,
    fetch_html as fa_fetch_html,
    write_csv as fa_write_csv,
)
from strava_to_gpx import (
    build_gpx as strava_build_gpx,
    create_web_session as strava_create_web_session,
    fetch_activity_web as strava_fetch_activity_web,
    fetch_streams as strava_fetch_streams,
    fetch_streams_web as strava_fetch_streams_web,
    get_access_token as strava_get_access_token,
    load_session_cookie_from_browser as strava_load_session_cookie_from_browser,
    parse_activity_id as strava_parse_activity_id,
    strava_get,
)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
STRAVA_OUTPUT_DIR = OUTPUT_DIR / "courses"
FLIGHT_OUTPUT_DIR = OUTPUT_DIR / "flights"


def cmd_strava(args: argparse.Namespace) -> int:
    activity_id = strava_parse_activity_id(args.activity)
    output_path = Path(args.output) if args.output else STRAVA_OUTPUT_DIR / f"activity_{activity_id}.gpx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.no_api:
        session_cookie = args.session_cookie
        if not session_cookie:
            session_cookie = strava_load_session_cookie_from_browser(args.browser_cookie)

        web_session = strava_create_web_session(session_cookie)
        activity = strava_fetch_activity_web(web_session, activity_id, local_tz=args.local_tz)
        streams = strava_fetch_streams_web(web_session, activity_id)
    else:
        token = strava_get_access_token()
        activity = strava_get(token, f"/activities/{activity_id}")
        streams = strava_fetch_streams(token, activity_id)

    gpx = strava_build_gpx(activity, streams)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(gpx.to_xml())

    print(f"GPX created: {output_path}")
    print(f"Exported points: {len(gpx.tracks[0].segments[0].points)}")
    return 0


def cmd_flightaware(args: argparse.Namespace) -> int:
    html = fa_fetch_html(args.url)
    points = fa_extract_track_points(html)
    ident = fa_extract_ident(html)
    output = Path(args.output) if args.output else FLIGHT_OUTPUT_DIR / f"{ident}_track.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    fa_write_csv(points, output)

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
        "--no-api",
        action="store_true",
        help="Use Strava website session cookie instead of official API token.",
    )
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
        "-o",
        "--output",
        default=None,
        help="Output GPX path (default: activity_<id>.gpx)",
    )
    strava.set_defaults(func=cmd_strava)

    flightaware = sub.add_parser("flightaware", help="Export FlightAware history page to CSV")
    flightaware.add_argument("url", help="FlightAware history URL")
    flightaware.add_argument("-o", "--output", default=None, help="Output CSV path")
    flightaware.set_defaults(func=cmd_flightaware)

    return parser


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.provider == "strava" and args.session_cookie is None:
        # Keep compatibility with existing env-based workflow.
        import os

        args.session_cookie = os.getenv("STRAVA_SESSION_COOKIE")

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(run())
