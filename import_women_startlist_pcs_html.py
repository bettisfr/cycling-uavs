#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Import Giro Women PCS startlist HTML into teams/riders/stage_links.")
    ap.add_argument("--competition-dir", required=True)
    ap.add_argument("--html-file", required=True)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.competition_dir).resolve()
    src = Path(args.html_file).resolve()

    soup = BeautifulSoup(src.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    team_blocks = soup.select("ul.startlist_v4 > li.slxl_iv")
    if not team_blocks:
        raise SystemExit("No team blocks found in HTML")

    teams: list[dict] = []
    riders: list[dict] = []

    for ti, block in enumerate(team_blocks, start=1):
        team_a = block.select_one("a.team")
        if not team_a:
            continue
        team_name = team_a.get_text(" ", strip=True)
        tid = f"T{ti:02d}"
        teams.append({"team_id": tid, "team_name": team_name, "country": None})

        seen_bibs: dict[int, dict] = {}
        for li in block.select("ul > li"):
            bib_el = li.select_one("span.bib")
            rider_a = li.select_one('a[href*="/rider/"]')
            if not bib_el or not rider_a:
                continue
            bib_txt = bib_el.get_text(strip=True)
            if not bib_txt.isdigit():
                continue
            bib = int(bib_txt)

            name = rider_a.get_text(" ", strip=True)
            name = re.sub(r"\s*\*+$", "", name).strip()

            flag = li.select_one("span.flag")
            nat = None
            if flag and flag.get("class"):
                cls = [c for c in flag.get("class", []) if c != "flag"]
                nat = cls[0].upper() if cls else None

            txt = li.get_text(" ", strip=True)
            dropout = ("dropout" in (li.get("class") or [])) or ("(DNF" in txt) or ("(DSQ" in txt)

            cand = {
                "bib": bib,
                "name": name,
                "nationality": nat,
                "team_id": tid,
                "team_name": team_name,
                "dropout": dropout,
            }
            cur = seen_bibs.get(bib)
            if cur is None or (cur["dropout"] and not dropout):
                seen_bibs[bib] = cand

        for bib in sorted(seen_bibs):
            c = seen_bibs[bib]
            riders.append(
                {
                    "rider_id": f"B{c['bib']:03d}",
                    "bib": c["bib"],
                    "name": c["name"],
                    "nationality": c["nationality"],
                    "team_id": c["team_id"],
                    "team_name": c["team_name"],
                    "strava_athlete_url": None,
                    "withdraw_stage": -1,
                }
            )

    uniq: dict[int, dict] = {}
    for r in riders:
        uniq[r["bib"]] = r
    riders = [uniq[b] for b in sorted(uniq)]

    teams_payload = {
        "version": 2,
        "race": "Giro d'Italia Women",
        "edition": 2026,
        "notes": f"Imported from PCS HTML: {src}",
        "teams": teams,
    }
    riders_payload = {
        "version": 2,
        "race": "Giro d'Italia Women",
        "edition": 2026,
        "notes": f"Imported from PCS HTML: {src}. nationality uses PCS flag code.",
        "riders": riders,
    }

    (root / "teams.json").write_text(json.dumps(teams_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (root / "riders.json").write_text(
        json.dumps(riders_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    stages = json.loads((root / "stages.json").read_text(encoding="utf-8"))["stages"]
    for st in stages:
        sid = st["stage_id"]
        p = root / "stage_links" / f"{sid}.json"
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        new = {
            "version": 1,
            "stage_id": sid,
            "date": st["date"],
            "statuses": ["found_public", "private_or_missing", "not_checked"],
            "activities": [],
        }
        old_map = {a.get("rider_id"): a for a in old.get("activities", []) if isinstance(a, dict)}
        for r in riders:
            rid = r["rider_id"]
            prev = old_map.get(rid, {})
            row = {
                "rider_id": rid,
                "status": prev.get("status", "not_checked"),
                "activity_url": prev.get("activity_url"),
                "locked": bool(prev.get("locked", False)),
            }
            for k in ("gpx_start_hhmm", "gpx_km", "gpx_path", "gpx_file"):
                if k in prev:
                    row[k] = prev[k]
            new["activities"].append(row)
        p.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"teams={len(teams)} riders={len(riders)}")
    if teams:
        print(f"first_team={teams[0]['team_name']}")
    if riders:
        print(f"bib_range={riders[0]['bib']}..{riders[-1]['bib']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
