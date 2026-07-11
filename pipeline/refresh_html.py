#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from pipeline.competition import load_competition
from pipeline.generate_rider_html import generate_rider_page
from pipeline.import_stage_raw import render_stage_html, render_stage_index_html


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate stage/rider HTML pages.")
    ap.add_argument("--competition-dir", required=True)
    ap.add_argument("--stage-id", default=None, help="Only regenerate one stage page (e.g. S03)")
    ap.add_argument("--skip-riders", action="store_true", help="Skip rider pages regeneration")
    args = ap.parse_args()

    comp = load_competition(args.competition_dir)
    base = comp.root
    stages = json.loads(comp.stages_json.read_text(encoding="utf-8"))["stages"]
    riders = json.loads(comp.riders_json.read_text(encoding="utf-8"))["riders"]

    stage_links: dict[str, dict] = {}
    for s in stages:
        sid = s["stage_id"]
        p = base / "stage_links" / f"{sid}.json"
        if p.exists():
            stage_links[sid] = json.loads(p.read_text(encoding="utf-8"))

    stage_count = 0
    print("Refreshing HTML pages...")
    if args.stage_id:
        render_stage_html(base, args.stage_id)
        stage_count = 1
        print(f"[OK] stage page: {args.stage_id}")
    else:
        for s in stages:
            render_stage_html(base, s["stage_id"])
            stage_count += 1
        print(f"[OK] stage pages: {stage_count}")
    render_stage_index_html(base)
    print("[OK] stage index")

    rider_count = 0
    if not args.skip_riders:
        for rider in riders:
            generate_rider_page(base, rider, stages, stage_links)
            rider_count += 1
        print(f"[OK] rider pages: {rider_count}")
    else:
        print("[SKIP] rider pages")

    print()
    print("=== Refresh Summary ===")
    print(f"stage_pages: {stage_count}")
    print("stage_index: 1")
    print(f"rider_pages: {rider_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
