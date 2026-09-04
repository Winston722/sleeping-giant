"""Does this repo capture everything dave-ledger needs to stop fetching Sleeper?

The owner made this repository the canonical Sleeper acquisition (2026-09-03),
which means dave-ledger derives its artifacts from the files committed here and
makes no API call of its own. That only works if everything it needs is here.
Two of the required files did NOT exist before that decision, and the failure
mode if either regresses is quiet: dave-ledger's teams file silently loses
every player field, or its matchups file silently empties.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sync  # noqa: E402


def test_the_trimmed_player_file_carries_the_fields_dave_ledger_derives_from():
    """WITHOUT THIS FILE THE ROSTER IS UNREADABLE.

    A roster's `players` is a list of bare Sleeper ids. gsis_id, position, age
    and years_exp exist only in the player dictionary, which is 5MB and stays
    gitignored -- so the trimmed copy is the only committed path from an id to
    a player. A regression here does not error; it produces a teams file where
    every player is an id and nothing else.
    """
    rosters = [{"roster_id": 1, "players": ["1234", "5678"]}]
    players = {
        "1234": {"player_id": "1234", "gsis_id": "00-0031234",
                 "full_name": "A Player", "position": "WR", "age": 27,
                 "years_exp": 5, "search_rank": 99, "injury_notes": "x"},
        "5678": {"player_id": "5678", "gsis_id": "00-0035678",
                 "full_name": "B Player", "position": "RB"},
        "9999": {"player_id": "9999", "full_name": "Not Rostered"},
    }
    out = sync.rostered_player_records(rosters, players)

    assert set(out) == {"1234", "5678"}, "must not carry unrostered players"
    assert out["1234"]["gsis_id"] == "00-0031234"
    assert out["1234"]["position"] == "WR"
    assert out["1234"]["years_exp"] == 5
    # platform bookkeeping is dropped
    assert "search_rank" not in out["1234"]
    assert "injury_notes" not in out["1234"]


def test_an_unresolvable_rostered_player_is_kept_not_dropped():
    """A silently shorter file reads downstream as a SMALLER ROSTER rather
    than as a player nobody could resolve, which is a different and wrong
    fact about the league."""
    rosters = [{"roster_id": 1, "players": ["1234", "nosuch"]}]
    out = sync.rostered_player_records(rosters, {"1234": {"player_id": "1234"}})
    assert set(out) == {"1234", "nosuch"}
    assert out["nosuch"]["unresolved"] is True


def test_the_player_fields_match_the_consumer_this_file_feeds():
    """dave-ledger's PLAYER_FIELDS and this one must stay equal.

    The two repositories do not import from each other, so nothing mechanical
    keeps them together. Pinning the list here means a field removed on this
    side fails a test rather than arriving downstream as a column of nulls.
    """
    assert sync.PLAYER_FIELDS == (
        "player_id", "gsis_id", "full_name", "first_name", "last_name",
        "position", "fantasy_positions", "team", "age", "years_exp",
        "birth_date", "status")


def test_preseason_week_is_not_mistaken_for_a_played_week():
    """Sleeper's preseason `week` counts DOWN to the opener, so week 2 in
    August is not week 2 of anything. Sweeping it requests matchups for weeks
    that have not happened."""
    assert sync.weeks_played(
        {"season_type": "pre", "season": "2026", "week": 2},
        {"season": "2026"}) == 0


def test_a_state_from_another_season_reports_no_played_weeks():
    """During the offseason gap the NFL state and the league disagree, and
    using the state anyway sweeps last year's weeks into this year's file."""
    assert sync.weeks_played(
        {"season_type": "regular", "season": "2025", "week": 17},
        {"season": "2026"}) == 0


def test_a_missing_or_unparseable_week_is_zero_never_a_default():
    """Guessing here silently invents a results record."""
    league = {"season": "2026"}
    for week in (None, "", "not-a-number", {}):
        assert sync.weeks_played(
            {"season_type": "regular", "season": "2026", "week": week},
            league) == 0


def test_a_real_regular_season_week_is_reported():
    """The guards must not be so eager that they refuse the true case."""
    assert sync.weeks_played(
        {"season_type": "regular", "season": "2026", "week": 5},
        {"season": "2026"}) == 5
