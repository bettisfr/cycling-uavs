#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from competition import load_competition

ACT_RE = re.compile(r"/activities/(\d+)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild courses/SXX GPX links from stage_links and gpx_store.")
    ap.add_argument("--competition-dir", required=True)
    ap.add_argument("--copy-instead-of-symlink", action="store_true")
    ap.add_argument("--seed-store-from-courses", action="store_true", help="Copy existing courses/SXX GPX into gpx_store before relinking.")
    args = ap.parse_args()

    comp = load_competition(args.competition_dir)
    links_dir = comp.stage_links_dir
    courses_dir = comp.courses_dir
    store_dir = comp.gpx_store_dir
    courses_dir.mkdir(parents=True, exist_ok=True)
    store_dir.mkdir(parents=True, exist_ok=True)

    linked = 0
    missing_store = 0
    removed_extra = 0
    seeded = 0

    if args.seed_store_from_courses:
        for gp in courses_dir.glob("S*/B*__activity_*.gpx"):
            dst = store_dir / gp.name
            if dst.exists() and dst.stat().st_size > 0:
                continue
            if gp.exists() and gp.stat().st_size > 0:
                shutil.copy2(gp, dst)
                seeded += 1

    for stage_file in sorted(links_dir.glob("S*.json")):
        sid = stage_file.stem
        stage_dir = courses_dir / sid
        stage_dir.mkdir(parents=True, exist_ok=True)

        payload = json.loads(stage_file.read_text(encoding="utf-8"))
        expected: set[str] = set()
        for row in payload.get("activities", []):
            rid = str(row.get("rider_id", "")).strip()
            url = str(row.get("activity_url") or "").strip()
            if not rid or not url:
                continue
            m = ACT_RE.search(url)
            if not m:
                continue
            expected.add(f"{rid}__activity_{m.group(1)}.gpx")

        # Remove extras from courses/SXX
        for gp in stage_dir.glob("B*__activity_*.gpx"):
            if gp.name not in expected:
                gp.unlink(missing_ok=True)
                removed_extra += 1

        # Ensure links for expected files
        for name in sorted(expected):
            src = store_dir / name
            dst = stage_dir / name
            if not src.exists() or src.stat().st_size == 0:
                missing_store += 1
                continue
            if dst.exists() or dst.is_symlink():
                dst.unlink(missing_ok=True)
            if args.copy_instead_of_symlink:
                shutil.copy2(src, dst)
            else:
                try:
                    dst.symlink_to(Path("..") / ".." / "gpx_store" / name)
                except Exception:
                    shutil.copy2(src, dst)
            linked += 1

    print(f"linked={linked}")
    print(f"seeded={seeded}")
    print(f"missing_store={missing_store}")
    print(f"removed_extra={removed_extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
