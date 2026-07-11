#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Step 3: regenerate maps, stage images, and HTML outputs.")
    ap.add_argument("--competition-dir", required=True)
    args = ap.parse_args()
    repo = Path(__file__).resolve().parents[1]
    cmds = [
        ["/home/fra/pyvenv/bin/python", "-m", "pipeline.visualize_tracks", "--competition-dir", args.competition_dir, "--all"],
        ["/home/fra/pyvenv/bin/python", "-m", "pipeline.generate_stage_images", "--competition-dir", args.competition_dir, "--all"],
        ["/home/fra/pyvenv/bin/python", "-m", "pipeline.refresh_html", "--competition-dir", args.competition_dir],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=repo)
        if r.returncode != 0:
            return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
