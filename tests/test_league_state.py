"""Tests for the pure league-state assembly — the part that runs without a network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sync import build_league_state  # noqa: E402


def test_collects_every_rostered_player():
    league = {"total_rosters": 2}
    rosters = [
        {"roster_id": 1, "players": ["4034", "6794"]},
        {"roster_id": 2, "players": ["11624", "4034"]},  # 4034 shared/duplicate
    ]
    state = build_league_state(league, rosters)

    assert state["id_type"] == "sleeper"
    assert state["num_teams"] == 2
    # Deduplicated and sorted.
    assert state["rostered_player_ids"] == ["11624", "4034", "6794"]


def test_num_teams_falls_back_to_roster_count():
    """A league response missing total_rosters still yields the right team count."""
    league = {}
    rosters = [{"players": ["1"]}, {"players": ["2"]}, {"players": ["3"]}]
    assert build_league_state(league, rosters)["num_teams"] == 3


def test_handles_empty_or_null_roster_players():
    """Preseason rosters can carry null player lists; that must not crash."""
    league = {"total_rosters": 2}
    rosters = [{"players": None}, {"players": []}]
    state = build_league_state(league, rosters)
    assert state["rostered_player_ids"] == []


def test_player_ids_are_strings():
    """Sleeper returns numeric-looking IDs; DAVE's crosswalk keys on strings."""
    league = {"total_rosters": 1}
    rosters = [{"players": [4034, 6794]}]
    state = build_league_state(league, rosters)
    assert state["rostered_player_ids"] == ["4034", "6794"]
    assert all(isinstance(p, str) for p in state["rostered_player_ids"])
