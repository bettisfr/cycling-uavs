#!/usr/bin/env python3
"""Export a Strava activity to GPX, including telemetry extensions."""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import gpxpy.gpx
import requests

STRAVA_WEB_BASE = "https://www.strava.com"
BROWSER_CHOICES = ["auto", "chrome", "chromium", "firefox", "edge", "brave", "opera"]

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
STRAVA_OUTPUT_DIR = OUTPUT_DIR / "courses"


def parse_activity_id(value: str) -> int:
    value = value.strip()
    if value.isdigit():
        return int(value)

    match = re.search(r"/activities/(\d+)", value)
    if match:
        return int(match.group(1))

    raise ValueError(
        "Could not extract activity id. Use an URL like "
        "https://www.strava.com/activities/123456789 or a numeric id."
    )


def create_web_session(session_cookie: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.strava.com/",
        }
    )
    session.cookies.set("_strava4_session", session_cookie, domain=".strava.com", path="/")
    return session


def load_session_cookie_from_browser(browser: str) -> str:
    try:
        import browser_cookie3
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install browser-cookie3 (pip install browser-cookie3)."
        ) from exc

    candidates = [browser] if browser != "auto" else BROWSER_CHOICES[1:]
    loaders: dict[str, Any] = {
        "chrome": browser_cookie3.chrome,
        "chromium": browser_cookie3.chromium,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
        "brave": browser_cookie3.brave,
        "opera": browser_cookie3.opera,
    }

    errors: list[str] = []
    for candidate in candidates:
        loader = loaders.get(candidate)
        if not loader:
            continue
        try:
            jar = loader(domain_name="strava.com")
            for cookie in jar:
                if cookie.name == "_strava4_session" and cookie.value:
                    return cookie.value
            errors.append(f"{candidate}: _strava4_session cookie not found")
        except Exception as exc:  # pragma: no cover
            errors.append(f"{candidate}: {exc}")

    raise RuntimeError(
        "Could not read _strava4_session from browser. "
        "Details: " + "; ".join(errors)
    )


def parse_local_epoch(epoch_value: int, local_tz: str | None) -> datetime:
    # Strava startDateLocal numeric fields appear to encode local wall time.
    # If no timezone hint is provided, keep historical UTC-like behavior.
    dt_utc_like = datetime.fromtimestamp(epoch_value, tz=timezone.utc)
    if not local_tz:
        return dt_utc_like
    tz = ZoneInfo(local_tz)
    local_wall = dt_utc_like.replace(tzinfo=None).replace(tzinfo=tz)
    return local_wall.astimezone(timezone.utc)


def fetch_activity_web(
    session: requests.Session, activity_id: int, local_tz: str | None = None
) -> dict[str, Any]:
    resp = session.get(f"{STRAVA_WEB_BASE}/activities/{activity_id}", timeout=30, allow_redirects=True)
    final_url = resp.url or ""
    if "/login" in final_url.lower() or "Log In | Strava" in resp.text:
        raise RuntimeError(
            "Invalid session or inaccessible activity. "
            "Update STRAVA_SESSION_COOKIE from a logged-in browser."
        )

    name_match = re.search(r"<title>(.*?)\s*\|\s*Strava</title>", resp.text, flags=re.IGNORECASE)
    activity_name = name_match.group(1).strip() if name_match else f"Strava Activity {activity_id}"

    start_date: str | None = None

    # Preferred source: pageView.activity().set({...}) blocks.
    for block in re.finditer(
        r"pageView\.activity\(\)\.set\(\{(?P<body>.*?)\}\);",
        resp.text,
        flags=re.DOTALL,
    ):
        body = block.group("body")
        for pattern in [r"startDateLocal\s*:\s*(\d+)", r"startDate\s*:\s*(\d+)"]:
            match = re.search(pattern, body)
            if not match:
                continue
            epoch = int(match.group(1))
            if "startDateLocal" in pattern:
                start_date = parse_local_epoch(epoch, local_tz).isoformat()
            else:
                start_date = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
            break
        if start_date:
            break

    # Fallbacks for alternate markup variants.
    if not start_date:
        for pattern in [
            r'"start_date"\s*:\s*"([^"]+)"',
            r"startDateLocal\s*:\s*(\d+)",
            r"startDate\s*:\s*(\d+)",
        ]:
            match = re.search(pattern, resp.text)
            if not match:
                continue
            value = match.group(1)
            if value.isdigit():
                epoch = int(value)
                if "startDateLocal" in pattern:
                    start_date = parse_local_epoch(epoch, local_tz).isoformat()
                else:
                    start_date = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
            else:
                start_date = value
            break

    return {"id": activity_id, "name": activity_name, "start_date": start_date}


def fetch_streams_web(session: requests.Session, activity_id: int) -> dict[str, Any]:
    stream_keys = [
        "time",
        "latlng",
        "distance",
        "altitude",
        "velocity_smooth",
        "heartrate",
        "cadence",
        "watts",
        "temp",
        "grade_smooth",
        "moving",
    ]

    resp = session.get(
        f"{STRAVA_WEB_BASE}/activities/{activity_id}/streams",
        params=[("stream_types[]", key) for key in stream_keys],
        timeout=30,
    )
    if resp.ok:
        payload = resp.json()
        if isinstance(payload, list):
            return {item.get("type"): item for item in payload if isinstance(item, dict)}
        if isinstance(payload, dict) and "latlng" in payload:
            return payload

    resp = session.get(
        f"{STRAVA_WEB_BASE}/activities/{activity_id}/streams",
        params={"keys": ",".join(stream_keys), "key_by_type": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else {}


def parse_start_time(activity: dict[str, Any]) -> datetime:
    raw = activity.get("start_date")
    if not raw:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def add_extension(point: gpxpy.gpx.GPXTrackPoint, tag: str, value: Any) -> None:
    if value is None:
        return
    elem = ET.Element(tag)
    elem.text = str(value)
    point.extensions.append(elem)


def get_stream_data(streams: dict[str, Any], key: str) -> list[Any]:
    value = streams.get(key, [])
    if isinstance(value, dict):
        data = value.get("data", [])
        return data if isinstance(data, list) else []
    if isinstance(value, list):
        return value
    return []


def build_gpx(activity: dict[str, Any], streams: dict[str, Any]) -> gpxpy.gpx.GPX:
    latlng_stream = get_stream_data(streams, "latlng")
    if not latlng_stream:
        raise RuntimeError(
            "latlng stream is not available. Check activity privacy/access permissions."
        )

    time_stream = get_stream_data(streams, "time")
    altitude_stream = get_stream_data(streams, "altitude")
    velocity_stream = get_stream_data(streams, "velocity_smooth")
    distance_stream = get_stream_data(streams, "distance")
    heartrate_stream = get_stream_data(streams, "heartrate")
    cadence_stream = get_stream_data(streams, "cadence")
    watts_stream = get_stream_data(streams, "watts")
    temp_stream = get_stream_data(streams, "temp")
    grade_stream = get_stream_data(streams, "grade_smooth")
    moving_stream = get_stream_data(streams, "moving")

    start_time = parse_start_time(activity)

    gpx = gpxpy.gpx.GPX()
    gpx.creator = "strava_to_gpx"
    gpx.name = activity.get("name") or f"Strava Activity {activity.get('id')}"
    gpx.description = f"Strava activity {activity.get('id')}"

    track = gpxpy.gpx.GPXTrack(name=gpx.name)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)
    gpx.tracks.append(track)

    for idx, coords in enumerate(latlng_stream):
        lat, lon = coords
        elevation = altitude_stream[idx] if idx < len(altitude_stream) else None

        point_time = None
        if idx < len(time_stream):
            point_time = start_time + timedelta(seconds=int(time_stream[idx]))

        point = gpxpy.gpx.GPXTrackPoint(
            latitude=float(lat),
            longitude=float(lon),
            elevation=float(elevation) if elevation is not None else None,
            time=point_time,
        )

        if idx < len(velocity_stream):
            point.speed = float(velocity_stream[idx])

        add_extension(point, "strava_distance_m", distance_stream[idx] if idx < len(distance_stream) else None)
        add_extension(point, "strava_heartrate_bpm", heartrate_stream[idx] if idx < len(heartrate_stream) else None)
        add_extension(point, "strava_cadence_rpm", cadence_stream[idx] if idx < len(cadence_stream) else None)
        add_extension(point, "strava_watts", watts_stream[idx] if idx < len(watts_stream) else None)
        add_extension(point, "strava_temp_c", temp_stream[idx] if idx < len(temp_stream) else None)
        add_extension(point, "strava_grade_smooth", grade_stream[idx] if idx < len(grade_stream) else None)
        add_extension(point, "strava_moving", moving_stream[idx] if idx < len(moving_stream) else None)

        segment.points.append(point)

    return gpx


def run() -> int:
    parser = argparse.ArgumentParser(
        description="Export a Strava activity URL/ID to GPX with stream data (web session only)."
    )
    parser.add_argument("activity", help="Strava activity URL or numeric id")
    parser.add_argument(
        "--session-cookie",
        default=os.getenv("STRAVA_SESSION_COOKIE"),
        help="Value of _strava4_session cookie (or env STRAVA_SESSION_COOKIE).",
    )
    parser.add_argument(
        "--browser-cookie",
        choices=BROWSER_CHOICES,
        default="auto",
        help="Read _strava4_session directly from browser cookies (default: auto).",
    )
    parser.add_argument(
        "--local-tz",
        default=os.getenv("STRAVA_LOCAL_TZ"),
        help="Timezone hint for numeric startDateLocal fields (example: Europe/Rome).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output GPX path (default: output/courses/activity_<id>.gpx)",
    )
    args = parser.parse_args()

    activity_id = parse_activity_id(args.activity)
    output_path = Path(args.output) if args.output else STRAVA_OUTPUT_DIR / f"activity_{activity_id}.gpx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    session_cookie = args.session_cookie
    if not session_cookie:
        session_cookie = load_session_cookie_from_browser(args.browser_cookie)
    web_session = create_web_session(session_cookie)
    activity = fetch_activity_web(web_session, activity_id, local_tz=args.local_tz)
    streams = fetch_streams_web(web_session, activity_id)

    gpx = build_gpx(activity, streams)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(gpx.to_xml())

    print(f"GPX created: {output_path}")
    print(f"Exported points: {len(gpx.tracks[0].segments[0].points)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            detail = f" [{exc.response.status_code}] {exc.response.text}"
        print(f"Strava HTTP error:{detail}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
