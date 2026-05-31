#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path


def pretty_case_token(tok: str, particles: set[str]) -> str:
    if "-" in tok:
        return "-".join(pretty_case_token(p, particles) for p in tok.split("-"))
    if "'" in tok:
        return "'".join(pretty_case_token(p, particles) for p in tok.split("'"))
    low = tok.lower()
    if low in particles:
        return low
    if tok.isupper() and len(tok) <= 3:
        return tok
    return tok[:1].upper() + tok[1:].lower() if tok else tok


def has_lower(s: str) -> bool:
    return any(c.islower() for c in s)


def main() -> int:
    root = Path("/home/fra/Desktop/github/cycling-uavs/giro_2026_w")
    teams_p = root / "teams.json"
    riders_p = root / "riders.json"

    teams_payload = json.loads(teams_p.read_text(encoding="utf-8"))
    riders_payload = json.loads(riders_p.read_text(encoding="utf-8"))

    for t in teams_payload.get("teams", []):
        name = str(t.get("team_name", ""))
        name = re.sub(r"\s*\((WTW|CTW|PRW)\)\s*$", "", name).strip()
        t["team_name"] = name

    team_name_by_id = {t["team_id"]: t["team_name"] for t in teams_payload.get("teams", [])}
    particles = {"de", "del", "della", "di", "da", "du", "des", "van", "der", "den", "von", "la", "le", "el"}

    for r in riders_payload.get("riders", []):
        raw = str(r.get("name", "")).strip()
        raw = re.sub(r"\s*\*+$", "", raw).strip()
        toks = raw.split()
        if not toks:
            continue

        cut = None
        for i, tk in enumerate(toks):
            if has_lower(tk):
                cut = i
                break
        if cut is None:
            cut = max(1, len(toks) - 1)

        surname = toks[:cut]
        given = toks[cut:]
        if not given:
            given = toks[-1:]
            surname = toks[:-1]

        given_fmt = " ".join(pretty_case_token(t, particles) for t in given)
        surname_fmt = " ".join(pretty_case_token(t, particles) for t in surname)
        norm = (given_fmt + " " + surname_fmt).strip()
        norm = re.sub(r"\s+", " ", norm)

        r["name"] = norm
        tid = r.get("team_id")
        if tid in team_name_by_id:
            r["team_name"] = team_name_by_id[tid]

    teams_payload["version"] = int(teams_payload.get("version", 1)) + 1
    riders_payload["version"] = int(riders_payload.get("version", 1)) + 1

    teams_p.write_text(json.dumps(teams_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    riders_p.write_text(json.dumps(riders_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("teams_updated", len(teams_payload.get("teams", [])))
    print("riders_updated", len(riders_payload.get("riders", [])))
    print("sample_names", [riders_payload["riders"][i]["name"] for i in range(min(8, len(riders_payload["riders"])))])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
