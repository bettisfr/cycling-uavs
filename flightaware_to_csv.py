#!/usr/bin/env python3
"""Export FlightAware history page track to CSV.

Input example:
https://it.flightaware.com/live/flight/MSA94S/history/20260323/2110Z/LICA/LIPO
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
FLIGHT_OUTPUT_DIR = OUTPUT_DIR / "flights"


def fetch_html(url: str) -> str:
    resp = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def extract_track_points(html: str) -> list[dict[str, Any]]:
    marker = '"track":'
    start = html.find(marker)
    if start == -1:
        raise RuntimeError("'track' field not found in FlightAware page.")

    decoder = json.JSONDecoder()
    payload = html[start + len(marker) :]
    payload = payload.lstrip()
    if not payload.startswith("["):
        raise RuntimeError("Unexpected format: 'track' is not a JSON list.")

    points, _ = decoder.raw_decode(payload)
    if not isinstance(points, list) or not points:
        raise RuntimeError("Track is empty or invalid.")
    return points


def extract_ident(html: str, fallback: str = "flightaware_track") -> str:
    for pattern in [r'"displayIdent":"([^"]+)"', r'"ident":"([^"]+)"']:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return fallback


def ts_to_iso(ts: int | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def write_csv(points: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp_unix",
                "timestamp_utc",
                "lat",
                "lon",
                "alt_ft",
                "groundspeed_kt",
                "type",
                "isolated",
            ]
        )
        for p in points:
            coord = p.get("coord") or [None, None]
            lat = coord[1] if isinstance(coord, list) and len(coord) > 1 else None
            lon = coord[0] if isinstance(coord, list) and len(coord) > 1 else None
            ts = p.get("timestamp")
            alt_raw = p.get("alt")
            alt_ft = ""
            if alt_raw is not None:
                try:
                    alt_ft = int(round(float(alt_raw) * 100))
                except (TypeError, ValueError):
                    alt_ft = ""
            writer.writerow(
                [
                    ts if ts is not None else "",
                    ts_to_iso(ts) if ts is not None else "",
                    lat if lat is not None else "",
                    lon if lon is not None else "",
                    alt_ft,
                    p.get("gs", ""),
                    p.get("type", ""),
                    p.get("isolated", ""),
                ]
            )


def run() -> int:
    parser = argparse.ArgumentParser(description="Export FlightAware track points to CSV.")
    parser.add_argument("url", help="FlightAware history URL")
    parser.add_argument("-o", "--output", default=None, help="Output CSV path")
    args = parser.parse_args()

    html = fetch_html(args.url)
    points = extract_track_points(html)
    ident = extract_ident(html)
    output = Path(args.output) if args.output else FLIGHT_OUTPUT_DIR / f"{ident}_track.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(points, output)

    print(f"CSV created: {output}")
    print(f"Points: {len(points)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            detail = f" [{exc.response.status_code}]"
        print(f"HTTP error{detail}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
