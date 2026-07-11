"""Official temporal bounds for Giro stage instances."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def stage_windows_path() -> Path:
    return Path(__file__).resolve().parents[1] / "stage_windows.json"


def official_window_utc(stage_id: str) -> tuple[int, int]:
    data = json.loads(stage_windows_path().read_text(encoding="utf-8"))
    stage = data["stages"][stage_id.upper()]
    if stage.get("stage_type") == "individual_time_trial":
        start_text = stage["first_rider_start"]
        finish_text = stage["last_starter_finish"]
    else:
        start_text = stage["start"]
        finish_text = stage["last_finish"]
    zone = ZoneInfo(data["time_zone"])
    start = datetime.fromisoformat(f'{stage["date"]}T{start_text}').replace(tzinfo=zone)
    finish = datetime.fromisoformat(f'{stage["date"]}T{finish_text}').replace(tzinfo=zone)
    return int(start.timestamp()), int(finish.timestamp())
