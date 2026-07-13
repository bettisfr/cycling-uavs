"""Shared geometric and race-state types."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol


EARTH_RADIUS_M = 6_371_000.0


class Positioned(Protocol):
    lat: float
    lon: float


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


@dataclass(frozen=True)
class Station:
    lat: float
    lon: float
    label: str


@dataclass(frozen=True)
class Cluster:
    lat: float
    lon: float
    weight: float
    rider_count: int = 0
    role: str = ""
    route_progress_m: float = 0.0


def distance_m(a: Positioned, b: Positioned) -> float:
    """Approximate local ground distance between two latitude/longitude points."""
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    mean_lat = (lat1 + lat2) / 2.0
    x = dlon * math.cos(mean_lat)
    return EARTH_RADIUS_M * math.hypot(x, dlat)
