#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

PCS_BASE = "https://www.procyclingstats.com"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
STRAVA_RE = re.compile(r"https?://(?:www\.)?strava\.com/(?:pros|athletes)/[^\"'\s<>?#]+(?:\?[^\"'\s<>#]*)?")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Extract Strava athlete links from PCS rider pages listed in a PCS startlist HTML.")
    ap.add_argument("--competition-dir", required=True, help="Directory containing riders.json.")
    ap.add_argument("--startlist-html", required=True, help="Saved PCS startlist HTML file.")
    ap.add_argument("--apply", action="store_true", help="Apply updates to riders.json.")
    ap.add_argument("--sleep-sec", type=float, default=1.5, help="Sleep between HTTP requests.")
    ap.add_argument("--timeout-sec", type=float, default=20.0, help="Request timeout.")
    ap.add_argument("--cache-dir", default=None, help="Optional dir with cached rider HTML pages.")
    ap.add_argument("--save-cache", action="store_true", help="Save fetched rider HTML pages to --cache-dir.")
    ap.add_argument("--output-json", default=None, help="Optional output JSON report path.")
    return ap.parse_args()


def norm_strava(url: str) -> str:
    u = url.strip()
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"\?hl=[^&]+", "", u)
    u = u.replace("http://", "https://")
    return u.rstrip("/")


def parse_startlist(startlist_html: Path) -> list[dict[str, Any]]:
    soup = BeautifulSoup(startlist_html.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    out: list[dict[str, Any]] = []
    for li in soup.select("ul.startlist_v4 > li.slxl_iv ul > li"):
        bib_el = li.select_one("span.bib")
        rider_a = li.select_one('a[href*="/rider/"]')
        if not bib_el or not rider_a:
            continue
        bib_txt = bib_el.get_text(strip=True)
        if not bib_txt.isdigit():
            continue
        href = (rider_a.get("href") or "").strip()
        if not href:
            continue
        url = href if href.startswith("http") else urljoin(PCS_BASE, href)
        out.append(
            {
                "bib": int(bib_txt),
                "name_raw": rider_a.get_text(" ", strip=True),
                "pcs_url": url,
            }
        )
    uniq: dict[int, dict[str, Any]] = {}
    for row in out:
        uniq[row["bib"]] = row
    return [uniq[b] for b in sorted(uniq)]


def load_html(url: str, cache_file: Path | None, timeout_sec: float) -> tuple[str | None, str]:
    if cache_file and cache_file.exists():
        return cache_file.read_text(encoding="utf-8", errors="ignore"), "cache"
    try:
        r = requests.get(url, timeout=timeout_sec, headers={"User-Agent": UA})
    except Exception as e:
        return None, f"error:{type(e).__name__}"
    if r.status_code != 200:
        return None, f"http:{r.status_code}"
    return r.text, "web"


def extract_strava(html: str) -> str | None:
    matches = STRAVA_RE.findall(html)
    if not matches:
        return None
    return norm_strava(matches[0])


def main() -> int:
    args = parse_args()
    comp_dir = Path(args.competition_dir).resolve()
    startlist_html = Path(args.startlist_html).resolve()
    riders_path = comp_dir / "riders.json"
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    riders_payload = json.loads(riders_path.read_text(encoding="utf-8"))
    riders = riders_payload.get("riders", [])
    by_bib = {int(r["bib"]): r for r in riders if isinstance(r, dict) and str(r.get("bib", "")).isdigit()}
    entries = parse_startlist(startlist_html)

    report: list[dict[str, Any]] = []
    scanned = 0
    found = 0
    updated = 0
    blocked = 0

    for i, e in enumerate(entries, start=1):
        bib = int(e["bib"])
        rider = by_bib.get(bib)
        if rider is None:
            report.append({"bib": bib, "pcs_url": e["pcs_url"], "status": "bib_not_in_riders"})
            continue

        cache_file = cache_dir / f"B{bib:03d}.html" if cache_dir else None
        html, src = load_html(e["pcs_url"], cache_file, args.timeout_sec)
        scanned += 1
        if html is None:
            if src.startswith("http:403"):
                blocked += 1
            report.append({"bib": bib, "name": rider.get("name"), "pcs_url": e["pcs_url"], "status": src})
            if args.sleep_sec > 0:
                time.sleep(args.sleep_sec)
            continue
        if args.save_cache and cache_file:
            cache_file.write_text(html, encoding="utf-8")

        strava = extract_strava(html)
        if not strava:
            report.append({"bib": bib, "name": rider.get("name"), "pcs_url": e["pcs_url"], "status": f"{src}:no_strava"})
        else:
            found += 1
            old = rider.get("strava_athlete_url")
            changed = old != strava
            if changed and args.apply:
                rider["strava_athlete_url"] = strava
                updated += 1
            report.append(
                {
                    "bib": bib,
                    "name": rider.get("name"),
                    "pcs_url": e["pcs_url"],
                    "status": f"{src}:found",
                    "strava_athlete_url": strava,
                    "changed": changed,
                }
            )
        if args.sleep_sec > 0:
            time.sleep(args.sleep_sec)
        print(f"[{i:03d}/{len(entries):03d}] B{bib:03d} {report[-1]['status']}")

    if args.apply and updated:
        riders_payload["version"] = int(riders_payload.get("version", 1)) + 1
        riders_path.write_text(json.dumps(riders_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    out_json = Path(args.output_json).resolve() if args.output_json else (comp_dir / "output" / "pcs_strava_links.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "competition_dir": str(comp_dir),
        "startlist_html": str(startlist_html),
        "entries": len(entries),
        "scanned": scanned,
        "found_strava": found,
        "updated_riders_json": updated if args.apply else 0,
        "blocked_403": blocked,
        "apply": bool(args.apply),
        "rows": report,
    }
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n=== PCS Strava Summary ===")
    print(f"entries={len(entries)} scanned={scanned} found={found} blocked_403={blocked}")
    print(f"updated={updated if args.apply else 0} apply={bool(args.apply)}")
    print(f"report={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
