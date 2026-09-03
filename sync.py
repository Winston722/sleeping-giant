#!/usr/bin/env python3
"""
Sleeper league sync.

Pulls one league's state from the Sleeper API and writes it to disk. Two kinds of
output:

  data/raw/*.json      verbatim API responses, committed so history is diffable
  data/league_state.json   the compact contract DAVE consumes (a flat list of
                           rostered player IDs, plus team count)

THIS REPOSITORY IS THE CANONICAL SLEEPER ACQUISITION (owner decision,
2026-09-03). `dave-ledger` used to run its own `scripts/fetch_sleeper_league.py`
against the same league on an overlapping schedule, so the same fact had two
scheduled writers and nothing kept them in agreement -- and DAVE derives
replacement level from who is rostered, so a divergence would quietly misprice
every free agent. dave-ledger now DERIVES from the files this script commits
(`--from-raw`) and makes no Sleeper call of its own.

That imposes a requirement this script did not previously have: **everything
dave-ledger needs to re-derive its artifacts must be committed here.** Two
things were added when the consolidation landed, and neither is optional:

  data/raw/players_rostered.json   the trimmed player records for rostered
                                   players only. The full dictionary is ~5MB
                                   and stays gitignored; this is a few hundred
                                   records carrying the fields a consumer
                                   needs, and WITHOUT IT dave-ledger's teams
                                   file loses gsis_id, age, years_exp and
                                   position -- it cannot be reconstructed from
                                   the roster lists, which are bare IDs.
  data/raw/matchups.json           per-week results, keyed by week. Empty until
                                   the season starts.

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
from typing import Any, Dict, List, Optional

BASE = "https://api.sleeper.app/v1"
ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"

# The fields worth keeping off a Sleeper player record. Everything else is
# platform bookkeeping -- injury notes, search ranks, headshot ids.
#
# THIS LIST MUST STAY EQUAL to `PLAYER_FIELDS` in dave-ledger's
# `scripts/fetch_sleeper_league.py`, because that script now derives its
# `teams.json` from the file this one writes. The duplication is deliberate:
# the two repositories do not import from each other, and a shared package for
# twelve strings would be worse than a comment saying where the other copy is.
# A field added there and not here arrives as `None` rather than as an error,
# so the dave-ledger side asserts the shape it received.
PLAYER_FIELDS = ("player_id", "gsis_id", "full_name", "first_name",
                 "last_name", "position", "fantasy_positions", "team",
                 "age", "years_exp", "birth_date", "status")


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


def weeks_played(state: Dict[str, Any], league: Dict[str, Any]) -> int:
    """How many regular-season weeks this league has actually played.

    Three guards, each because ignoring it fetches rows that look real and are
    not. Carried over verbatim in behaviour from dave-ledger's
    `fetch_sleeper_league.weeks_played`, which is where they were learned:

    * PRESEASON `week` counts DOWN to the opener, so `state.week == 2` in
      August is not week 2 of anything.
    * A STATE FROM A DIFFERENT SEASON than the league's says nothing about this
      league's progress; during the offseason gap the two disagree, and using
      it sweeps last year's weeks into this year's file.
    * A MISSING or unparseable week is zero, never a default, because guessing
      here silently invents a results record.
    """
    if str(state.get("season_type")) not in {"regular", "post"}:
        return 0
    if str(state.get("season")) != str(league.get("season")):
        return 0
    try:
        return max(0, int(state.get("week") or 0))
    except (TypeError, ValueError):
        return 0


def rostered_player_records(rosters: List[Dict[str, Any]],
                            players: Dict[str, Any]) -> Dict[str, Any]:
    """The trimmed player dictionary, restricted to players on a roster.

    The full dictionary is ~5MB and stays gitignored; this is a few hundred
    records. It is committed because dave-ledger derives `teams.json` from it
    and CANNOT reconstruct it from anything else here -- a roster's `players`
    is a list of bare Sleeper ids, carrying no gsis_id, position or age.

    A rostered id absent from the dictionary is kept as an explicit record
    rather than dropped, because a silently shorter file would read downstream
    as a smaller roster instead of as an unresolved player.
    """
    wanted = {str(pid) for r in rosters or [] for pid in (r.get("players") or [])}
    out: Dict[str, Any] = {}
    for pid in sorted(wanted):
        rec = players.get(pid)
        if rec is None:
            out[pid] = {"player_id": pid, "unresolved": True}
            continue
        out[pid] = {k: rec.get(k) for k in PLAYER_FIELDS}
    return out


def build_league_state(
    league: Dict[str, Any],
    rosters: List[Dict[str, Any]],
    players: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Derive DAVE's league_state contract from raw league and roster responses.

    Pure and side-effect free so it can be tested without the network. Every player
    appearing on any roster is rostered; everyone else is, by subtraction, a free
    agent — which is exactly what DAVE needs to compute replacement level from
    reality rather than assumption.

    When the Sleeper player dictionary is supplied, each rostered player also gets
    a resolved gsis_id, name and position. Sleeper carries a gsis for brand-new
    rookies before the public ID database does, so this is what lets DAVE resolve
    the players a crosswalk would otherwise miss.
    """
    rostered: List[str] = []
    for roster in rosters:
        rostered.extend(str(pid) for pid in (roster.get("players") or []))
    rostered = sorted(set(rostered))

    out = {
        "as_of": dt.date.today().isoformat(),
        "id_type": "sleeper",
        "num_teams": league.get("total_rosters") or len(rosters),
        "rostered_player_ids": rostered,
    }

    if players:
        records = []
        for sid in rostered:
            meta = players.get(sid) or {}
            records.append({
                "id": sid,
                "gsis_id": meta.get("gsis_id"),
                "name": meta.get("full_name")
                or " ".join(filter(None, [meta.get("first_name"), meta.get("last_name")])) or None,
                "position": meta.get("position"),
            })
        out["rostered_players"] = records
        resolved = sum(1 for r in records if r["gsis_id"])
        out["_gsis_resolved"] = resolved
    return out


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

    # The player dictionary is ~5MB and carries each player's gsis_id — the reliable
    # way to resolve brand-new rookies. Gitignored (not committed), fetched fresh.
    print("  fetching player dictionary (~5MB) for gsis resolution...")
    players = _get("players/nfl")
    (RAW / "players.json").write_text(json.dumps(players, sort_keys=True))

    # MATCHUPS are per-week and exist only once a week has been played. The whole
    # regular season is swept each run so a late first capture still assembles a
    # complete record rather than starting from today. Sleeper keeps serving past
    # weeks, so this is recoverable rather than a one-shot capture — it is swept
    # for completeness, not because a missed run loses it.
    matchups: Dict[str, Any] = {}
    played = weeks_played(state or {}, league)
    for week in range(1, played + 1):
        rows = _get(f"league/{league_id}/matchups/{week}") or []
        if rows:
            matchups[str(week)] = rows
    print(f"  {len(matchups)} weeks of matchups"
          f"{' (preseason: none yet)' if not matchups else ''}")

    # The trimmed dictionary dave-ledger derives from. See the module docstring:
    # without this its teams file loses every field but the bare Sleeper id.
    rostered = rostered_player_records(rosters, players)
    print(f"  {len(rostered)} rostered player records kept "
          f"(of {len(players):,} in Sleeper's database)")

    # Raw first, verbatim — interpretation happens only after the bytes are safe.
    for name, payload in [
        ("league", league), ("rosters", rosters), ("users", users),
        ("state", state), ("traded_picks", traded_picks),
        ("matchups", matchups), ("players_rostered", rostered),
    ]:
        (RAW / f"{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    league_state = build_league_state(league, rosters, players)
    (ROOT / "data" / "league_state.json").write_text(
        json.dumps(league_state, indent=2) + "\n"
    )

    # A one-line signal for the workflow log and for spotting drift.
    scoring_keys = len(league.get("scoring_settings") or {})
    print(
        f"  league: {league.get('name')!r}  season {league.get('season')}  "
        f"teams {league_state['num_teams']}  scoring keys {scoring_keys}"
    )
    n = len(league_state['rostered_player_ids'])
    resolved = league_state.get('_gsis_resolved', 0)
    print(f"  rostered players: {n}  ({resolved} with a Sleeper gsis)")
    print("  wrote data/raw/*.json and data/league_state.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - surface a clean failure to the CI log
        print(f"sync failed: {e}", file=sys.stderr)
        sys.exit(1)
