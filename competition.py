#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Competition:
    root: Path
    config: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.config.get("id", self.root.name))

    @property
    def timezone(self) -> str:
        return str(self.config.get("timezone", "Europe/Rome"))

    @property
    def flight_callsign(self) -> str:
        return str(self.config.get("flight_callsign", "ASR251"))

    @property
    def min_start_by_stage(self) -> dict[str, str]:
        rules = self.config.get("stage_rules", {})
        if not isinstance(rules, dict):
            return {}
        m = rules.get("min_start_hhmm_by_stage", {})
        return m if isinstance(m, dict) else {}

    @property
    def riders_json(self) -> Path:
        return self.root / "riders.json"

    @property
    def stages_json(self) -> Path:
        return self.root / "stages.json"

    @property
    def stage_links_dir(self) -> Path:
        return self.root / "stage_links"

    @property
    def courses_dir(self) -> Path:
        return self.root / "courses"

    @property
    def flights_dir(self) -> Path:
        return self.root / "flights"

    @property
    def gpx_store_dir(self) -> Path:
        return self.root / "gpx_store"

    @property
    def raw_riders_dir(self) -> Path:
        return self.root / "raw" / "riders"

    @property
    def raw_stages_dir(self) -> Path:
        return self.root / "raw" / "stages"

    @property
    def html_dir(self) -> Path:
        return self.root / "html"


def load_competition(competition_dir: str | Path) -> Competition:
    root = Path(competition_dir).resolve()
    cfg_path = root / "competition.json"
    if not cfg_path.exists():
        raise SystemExit(f"Missing competition config: {cfg_path}")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    return Competition(root=root, config=cfg)
