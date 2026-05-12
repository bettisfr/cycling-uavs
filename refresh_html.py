#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_rider_html import generate_rider_page
from import_stage_raw import render_stage_html, render_stage_index_html


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate stage/rider HTML pages.")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--stage-id", default=None, help="Only regenerate one stage page (e.g. S03)")
    ap.add_argument("--skip-riders", action="store_true", help="Skip rider pages regeneration")
    args = ap.parse_args()

    base = Path(args.dataset_dir)
    stages = json.loads((base / "stages.json").read_text(encoding="utf-8"))["stages"]
    riders = json.loads((base / "riders.json").read_text(encoding="utf-8"))["riders"]

    stage_links: dict[str, dict] = {}
    for s in stages:
        sid = s["stage_id"]
        p = base / "stage_links" / f"{sid}.json"
        if p.exists():
            stage_links[sid] = json.loads(p.read_text(encoding="utf-8"))

    stage_count = 0
    if args.stage_id:
        render_stage_html(base, args.stage_id)
        stage_count = 1
    else:
        for s in stages:
            render_stage_html(base, s["stage_id"])
            stage_count += 1
    render_stage_index_html(base)

    rider_count = 0
    if not args.skip_riders:
        for rider in riders:
            generate_rider_page(base, rider, stages, stage_links)
            rider_count += 1

    print(f"stage_pages={stage_count}")
    print("stage_index=1")
    print(f"rider_pages={rider_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

