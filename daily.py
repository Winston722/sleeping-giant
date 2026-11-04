"""A daily snapshot of what Sleeper shows every manager about the coming weeks (owner, 2026-11-04: "I want full numbers,
not fingerprints. I want this week's projection, next week's projection, and the week after that's projection, by day
throughout the week. Can you pull depth chart snapshots via Sleeper too?").

    python sync.py daily [--ahead 3] [--season 2026]

One run, at its own UTC time:
- **Projections** for the current NFL week and the two after (`/v1/projections/nfl/regular/{season}/{week}`, as
  `projections.py` keeps them: each player's projected statistics, no ranks or draft fields), written to
  `data/projections/daily/<season>/<YYYY-MM-DDTHHMMZ>/<week>.json`. They stay under `data/projections/`: gitignored,
  on the machine that pulled them (owner ruling 2026-10-17 §1, docs/sources-and-obligations.md C11).
- **Depth charts and designations** from Sleeper's player dictionary (`/v1/players/nfl`, what the app's Team tab
  shows): for every player on an NFL team, his team, position, fantasy positions, `depth_chart_position`,
  `depth_chart_order`, `injury_status`, `injury_body_part`, `injury_start_date`, `practice_participation` and roster
  `status`; written to `data/raw/depth/<YYYY-MM-DDTHHMMZ>.json.gz` (committed, as the daily injury lists are).

Three calls, paced. The NFL week is Sleeper's (`/v1/state/nfl`, `week`; the regular season only).
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import time
from pathlib import Path

from projections import PACE, week
from sync import ROOT, _get

KEEP = ("team", "position", "fantasy_positions", "depth_chart_position", "depth_chart_order", "injury_status",
        "injury_body_part", "injury_start_date", "practice_participation", "status")


def depth_records(players: dict) -> dict:
    """player_id -> the kept fields, for every player Sleeper lists on an NFL team."""
    return {pid: {k: p.get(k) for k in KEEP} for pid, p in players.items()
            if isinstance(p, dict) and p.get("team")}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ahead", type=int, default=3, help="the current week and the ones after: this many weeks")
    ap.add_argument("--season", type=int, default=None, help="default: Sleeper's current season")
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    a = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H%MZ")
    state = _get("state/nfl")
    season = int(a.season or state.get("season"))
    wk = int(state.get("week") or 0)
    if state.get("season_type") != "regular" or wk < 1:
        print(f"not the regular season ({state.get('season_type')}, week {wk}): nothing taken")
        return 0
    d = a.out / "projections" / "daily" / str(season) / stamp
    d.mkdir(parents=True, exist_ok=True)
    for w in range(wk, wk + a.ahead):
        proj = week(season, w)
        if proj:
            (d / f"{w}.json").write_text(json.dumps(proj, sort_keys=True, separators=(",", ":")))
        print(f"  {season} week {w}: {len(proj)} players projected", flush=True)
        time.sleep(PACE)
    players = _get("players/nfl")
    depth = depth_records(players)
    out = a.out / "raw" / "depth"
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"taken_at": now.isoformat(timespec="seconds"), "season": season, "week": wk, "players": depth},
                      sort_keys=True, separators=(",", ":")) + "\n"
    (out / f"{stamp}.json.gz").write_bytes(gzip.compress(body.encode(), mtime=0))
    print(f"  depth charts and designations: {len(depth)} players on NFL teams -> data/raw/depth/{stamp}.json.gz")
    print(f"projections for weeks {wk}-{wk + a.ahead - 1} -> {d.relative_to(a.out.parent)} (local only)")
    return 0
