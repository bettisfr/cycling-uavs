#!/usr/bin/env python3
from __future__ import annotations

import subprocess


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Step 3: regenerate maps, stage images, and HTML outputs.")
    ap.add_argument("--competition-dir", required=True)
    args = ap.parse_args()
    cmds = [
        ["/home/fra/pyvenv/bin/python", "visualize_tracks.py", "--competition-dir", args.competition_dir, "--all"],
        ["/home/fra/pyvenv/bin/python", "generate_stage_images.py", "--competition-dir", args.competition_dir, "--all"],
        ["/home/fra/pyvenv/bin/python", "refresh_html.py", "--competition-dir", args.competition_dir],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd)
        if r.returncode != 0:
            return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
