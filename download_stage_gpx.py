#!/usr/bin/env python3
"""Download GPX files for all activity URLs in a stage_links/Sxx.json file."""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import time
from pathlib import Path

import requests


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Download stage GPX files from stage_links JSON.')
    p.add_argument('--stage-id', default=None, help='Stage id, e.g. S01')
    p.add_argument('--all', action='store_true', help='Process all stage_links/SXX.json files.')
    p.add_argument('--repo-dir', default='/home/fra/Desktop/github/cycling-uavs')
    p.add_argument('--sleep', type=float, default=5.0, help='Base seconds between requests')
    p.add_argument('--jitter', type=float, default=1.0, help='Random seconds added to sleep')
    p.add_argument('--retries', type=int, default=2, help='Retries per activity on failure')
    p.add_argument('--local-tz', default='Europe/Rome')
    p.add_argument(
        '--one',
        action='store_true',
        help='Process only one missing activity (default processes all missing).',
    )
    args = p.parse_args()
    if not args.all and not args.stage_id:
        p.error('either --stage-id or --all is required')
    return args


def process_stage(args: argparse.Namespace, repo: Path, stage_id: str) -> dict[str, int]:
    stage_path = repo / 'giro_2026' / 'stage_links' / f'{stage_id}.json'
    cookie_path = repo / 'strava_session_cookie.txt'
    out_dir = repo / 'giro_2026' / 'courses' / stage_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if not stage_path.exists():
        print(f'\n=== {stage_id} ===')
        print(f'[SKIP] missing stage file: {stage_path}')
        return {'ok': 0, 'fail': 0, 'pending': 0, 'processed': 0, 'total': 0}

    stage = json.loads(stage_path.read_text(encoding='utf-8'))
    activities = [a for a in stage.get('activities', []) if a.get('activity_url')]

    cookie = cookie_path.read_text(encoding='utf-8').strip()
    if not cookie:
        raise RuntimeError('Empty strava_session_cookie.txt')

    ok = 0
    fail = 0
    fails: list[tuple[str, str, str]] = []

    pending: list[dict] = []
    for a in activities:
        rider_id = str(a.get('rider_id', 'UNKNOWN'))
        url = str(a.get('activity_url', '')).strip()
        m = re.search(r'/activities/(\d+)', url)
        if not m:
            continue
        aid = m.group(1)
        out = out_dir / f'{rider_id}__activity_{aid}.gpx'
        if out.exists() and out.stat().st_size > 0:
            continue
        pending.append(a)

    if not pending:
        print(f'\n=== {stage_id} ===')
        print('[OK] no missing GPX')
        return {'ok': 0, 'fail': 0, 'pending': 0, 'processed': 0, 'total': len(activities)}

    queue = pending[:1] if args.one else pending
    print(f'\n=== {stage_id} ===')
    print(f'activities_with_url={len(activities)} pending={len(pending)} processing={len(queue)}')

    for idx, a in enumerate(queue, start=1):
        rider_id = str(a.get('rider_id', 'UNKNOWN'))
        url = str(a.get('activity_url', '')).strip()
        m = re.search(r'/activities/(\d+)', url)
        if not m:
            fail += 1
            fails.append((rider_id, url, 'invalid activity url'))
            continue
        aid = m.group(1)
        out = out_dir / f'{rider_id}__activity_{aid}.gpx'

        cmd = [
            '/home/fra/pyvenv/bin/python',
            str(repo / 'lib' / 'strava_to_gpx.py'),
            '--session-cookie',
            cookie,
            '--local-tz',
            args.local_tz,
            url,
            '-o',
            str(out),
        ]

        success = False
        last_err = ''
        for attempt in range(args.retries + 1):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                success = True
                break
            last_err = (r.stderr or r.stdout or 'unknown error').strip()
            if attempt < args.retries:
                time.sleep(args.sleep + random.uniform(0.0, args.jitter))

        # Fallback: direct Strava GPX export endpoint (no gpxpy dependency).
        if not success:
            try:
                export_url = f'https://www.strava.com/activities/{aid}/export_gpx'
                rr = requests.get(
                    export_url,
                    cookies={'_strava4_session': cookie},
                    headers={'User-Agent': 'Mozilla/5.0'},
                    timeout=45,
                    allow_redirects=True,
                )
                text = rr.text if rr.text is not None else ''
                if rr.status_code == 200 and '<gpx' in text:
                    out.write_text(text, encoding='utf-8')
                    success = True
            except Exception as exc:
                if not last_err:
                    last_err = str(exc)

        if success:
            ok += 1
            print(f'[{idx:03d}/{len(queue):03d}] OK   {rider_id} {aid}')
        else:
            fail += 1
            fails.append((rider_id, url, last_err))
            print(f'[{idx:03d}/{len(queue):03d}] FAIL {rider_id} {aid}')

        time.sleep(args.sleep + random.uniform(0.0, args.jitter))

    print('summary:')
    print(f'  total_with_activity_url: {len(activities)}')
    print(f'  total_pending_before_run: {len(pending)}')
    print(f'  processed_now: {len(queue)}')
    print(f'  download_ok: {ok}')
    print(f'  download_fail: {fail}')
    print(f'  output_dir: {out_dir}')

    if fails:
        fail_log = out_dir / '_download_failures.txt'
        with fail_log.open('w', encoding='utf-8') as f:
            for rider_id, url, err in fails:
                f.write(f'{rider_id}\t{url}\t{err}\n')
        print(f'  failure_log: {fail_log}')

    return {'ok': ok, 'fail': fail, 'pending': len(pending), 'processed': len(queue), 'total': len(activities)}


def main() -> int:
    args = parse_args()
    repo = Path(args.repo_dir)

    if args.all:
        stage_dir = repo / 'giro_2026' / 'stage_links'
        stage_ids = sorted(p.stem for p in stage_dir.glob('S*.json'))
    else:
        stage_ids = [args.stage_id]

    grand = {'ok': 0, 'fail': 0, 'pending': 0, 'processed': 0, 'total': 0}
    for sid in stage_ids:
        res = process_stage(args, repo, sid)
        for k in grand:
            grand[k] += int(res[k])

    if len(stage_ids) > 1:
        print('\n=== GRAND TOTAL ===')
        print(f"stages: {len(stage_ids)}")
        print(f"total_with_activity_url: {grand['total']}")
        print(f"total_pending_before_run: {grand['pending']}")
        print(f"processed_now: {grand['processed']}")
        print(f"download_ok: {grand['ok']}")
        print(f"download_fail: {grand['fail']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
