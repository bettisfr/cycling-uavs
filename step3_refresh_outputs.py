#!/usr/bin/env python3
from __future__ import annotations

import subprocess


def main() -> int:
    cmds = [
        ["/home/fra/pyvenv/bin/python", "visualize_tracks.py", "--all"],
        ["/home/fra/pyvenv/bin/python", "generate_stage_images.py", "--all"],
        ["/home/fra/pyvenv/bin/python", "refresh_html.py"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd)
        if r.returncode != 0:
            return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
