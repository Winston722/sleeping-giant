"""The daily snapshot keeps depth charts and designations for players on NFL teams only, and only the kept fields."""
from daily import KEEP, depth_records


def test_depth_records_keep_team_players_and_the_kept_fields():
    players = {
        "1": {"team": "ARI", "position": "RB", "depth_chart_position": "RB", "depth_chart_order": 1,
              "injury_status": None, "full_name": "Kept", "search_rank": 5},
        "2": {"team": None, "position": "WR", "depth_chart_order": None},
        "3": "not a record",
    }
    out = depth_records(players)
    assert list(out) == ["1"]
    assert set(out["1"]) == set(KEEP)
    assert out["1"]["depth_chart_order"] == 1 and "full_name" not in out["1"]
