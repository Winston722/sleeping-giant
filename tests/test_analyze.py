"""analyze.py reads DAVE's published v5 board through the contract only."""

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze  # noqa: E402

COLUMNS = ["player_id", "full_name", "fantasy_group", "is_rookie", "value", "exp_s1", "exp_s2", "exp_s3",
           "age", "manager", "sleeper_id"]


def _row(pid, name, group, value, s1, sid, rookie=False, manager="A", age=25):
    return {"player_id": pid, "full_name": name, "fantasy_group": group, "is_rookie": rookie,
            "value": value, "exp_s1": s1, "exp_s2": s1 - 10, "exp_s3": s1 - 20, "age": age,
            "manager": manager, "sleeper_id": sid}


def _write(tmp_path, rows, columns=COLUMNS, sha=None):
    board = tmp_path / "draft_board.csv"
    with board.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    digest = sha or hashlib.sha256(board.read_bytes()).hexdigest()
    (tmp_path / "draft_board.meta.json").write_text(json.dumps(
        {"board_sha256": digest, "model_version": "v5", "origin_target": 2026}))
    return board


def test_board_is_refused_when_it_does_not_match_its_sidecar(tmp_path):
    board = _write(tmp_path, [_row("00-1", "Q", "QB", 10, 300, "1")], sha="0" * 64)
    with pytest.raises(SystemExit, match="board_sha256"):
        analyze.load_board(board)


def test_pre_v5_board_is_refused_by_its_columns(tmp_path):
    board = _write(tmp_path, [{"player_id": "00-1", "full_name": "Q", "fantasy_group": "QB"}],
                   columns=["player_id", "full_name", "fantasy_group"])
    with pytest.raises(SystemExit, match="predates the v5 contract"):
        analyze.load_board(board)


def test_board_rows_parse_and_group_by_sleeper_id(tmp_path):
    board = _write(tmp_path, [_row("00-1", "Edge", "DL", 50, 120, "7"), _row("00-1", "Edge", "LB", 80, 120, "7"),
                              _row("00-2", "Kid", "WR", 5, 40, "8", rookie=True)])
    rows, meta = analyze.load_board(board)
    ids = analyze.by_sleeper_id(rows)
    assert set(ids["7"]) == {"DL", "LB"} and ids["8"]["WR"]["is_rookie"] is True
    assert analyze.best_row(ids["7"])["fantasy_group"] == "LB"      # the better row is the headline
    assert rows[0]["value"] == 50.0 and meta["model_version"] == "v5"


def test_lineup_fills_restrictive_slots_first_and_starts_each_player_once():
    players = {
        "q1": {"QB": _row("1", "QB1", "QB", 0, 300, "q1")},
        "q2": {"QB": _row("2", "QB2", "QB", 0, 250, "q2")},
        "r1": {"RB": _row("3", "RB1", "RB", 0, 200, "r1")},
        "e": {"DL": _row("4", "Edge", "DL", 0, 90, "e"), "LB": _row("4", "Edge", "LB", 0, 90, "e")},
    }
    total, picks, unfilled = analyze.lineup(players, ["QB", "RB", "SUPER_FLEX", "DL", "LB", "BN", "K"], "exp_s1")
    slots = {slot: name for slot, name, _, _ in picks}
    assert slots["QB"] == "QB1" and slots["SUPER_FLEX"] == "QB2" and slots["RB"] == "RB1"
    assert sorted(unfilled) == ["K", "LB"]           # Edge starts once (DL); K has no board group
    assert total == 300 + 250 + 200 + 90


def test_team_rows_bench_ir_and_taxi_this_season_only():
    ids = {"1": {"QB": _row("00-1", "Star", "QB", 500, 300, "1")},
           "2": {"QB": _row("00-2", "Hurt", "QB", 400, 350, "2")}}
    league = {"roster_positions": ["QB", "BN"]}
    rosters = [{"owner_id": "u", "roster_id": 1, "players": ["1", "2", "9"], "reserve": ["2"], "taxi": []}]
    teams, absent = analyze.team_rows(ids, league, rosters, {"u": "A"})
    t = teams[0]
    assert t["s1"] == 300          # the IR player cannot start this season
    assert t["s2"] == 340          # but can next season (350 - 10)
    assert t["value_pos"] == 900 and t["on_board"] == 2 and absent == [("A", "9")]
    assert t["stale"] == 0
