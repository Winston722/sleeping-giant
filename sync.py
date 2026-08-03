#!/usr/bin/env python3
"""
Sleeper league sync.

Pulls one league's state from the Sleeper API and writes it to disk. Two kinds of
output:

  data/raw/*.json      verbatim API responses, committed so history is diffable
  data/league_state.json   the compact contract DAVE consumes (a flat list of
                           rostered player IDs, plus team count)

This runs where the network is open — a GitHub Actions runner — because the
question-answering environment cannot reach the Sleeper API. It writes raw
responses before deriving anything, so a parsing change never costs a refetch.

Standard library only, so the workflow needs no dependencies to install.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

BASE = "https://api.sleeper.app/v1"
ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"


def _get(path: str, retries: int = 4) -> Any:
    """GET a Sleeper endpoint as JSON, retrying transient failures with backoff."""
    url = f"{BASE}/{path}"
    delay = 2.0
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dave-sleeper-sync"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
    raise RuntimeError(f"Failed to GET {url} after {retries} attempts: {last}")


def build_league_state(league: Dict[str, Any], rosters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Derive DAVE's league_state contract from raw league and roster responses.

    Pure and side-effect free so it can be tested without the network. Every player
    appearing on any roster is rostered; everyone else is, by subtraction, a free
    agent — which is exactly what DAVE needs to compute replacement level from
    reality rather than assumption.
    """
    rostered: List[str] = []
    for roster in rosters:
        rostered.extend(str(pid) for pid in (roster.get("players") or []))

    return {
        "as_of": dt.date.today().isoformat(),
        "id_type": "sleeper",
        "num_teams": league.get("total_rosters") or len(rosters),
        "rostered_player_ids": sorted(set(rostered)),
    }


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text())
    league_id = str(cfg["league_id"])

    RAW.mkdir(parents=True, exist_ok=True)

    print(f"Syncing Sleeper league {league_id} ...")
    league = _get(f"league/{league_id}")
    rosters = _get(f"league/{league_id}/rosters")
    users = _get(f"league/{league_id}/users")
    state = _get("state/nfl")
    traded_picks = _get(f"league/{league_id}/traded_picks")

    # Raw first, verbatim — interpretation happens only after the bytes are safe.
    for name, payload in [
        ("league", league), ("rosters", rosters), ("users", users),
        ("state", state), ("traded_picks", traded_picks),
    ]:
        (RAW / f"{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    league_state = build_league_state(league, rosters)
    (ROOT / "data" / "league_state.json").write_text(
        json.dumps(league_state, indent=2) + "\n"
    )

    # A one-line signal for the workflow log and for spotting drift.
    scoring_keys = len(league.get("scoring_settings") or {})
    print(
        f"  league: {league.get('name')!r}  season {league.get('season')}  "
        f"teams {league_state['num_teams']}  scoring keys {scoring_keys}"
    )
    print(f"  rostered players: {len(league_state['rostered_player_ids'])}")
    print("  wrote data/raw/*.json and data/league_state.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - surface a clean failure to the CI log
        print(f"sync failed: {e}", file=sys.stderr)
        sys.exit(1)
