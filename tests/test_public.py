"""public.py: the crawl of public dynasty leagues for DAVE's real-league check (owner approval, 2026-10-09).

What must hold: nothing that names a person is written (display names, team and league names, chat), user ids are
keyed pseudonyms, only dynasty chains with enough complete seasons are kept, and a second run fetches nothing it
already has."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import public  # noqa: E402

KEY = b"k" * 32


def _league(lid, season, prev=None, typ=2, status="complete"):
    return {"league_id": lid, "season": season, "status": status, "previous_league_id": prev, "total_rosters": 2,
            "name": "The Secret League", "avatar": "abc", "metadata": {"latest_league_winner_roster_id": "1"},
            "last_author_display_name": "somebody", "last_message_text_map": {"x": "hi"},
            "settings": {"type": typ, "num_teams": 2, "playoff_teams": 2, "commissioner_note": "x"},
            "roster_positions": ["QB"], "scoring_settings": {"pass_td": 4}}


def _rosters(u1, u2):
    return [{"roster_id": 1, "owner_id": u1, "co_owners": None, "players": ["10"], "metadata": {"team_name": "Mine"},
             "settings": {"wins": 9, "fpts": 1500, "ppts": 1700}},
            {"roster_id": 2, "owner_id": u2, "co_owners": [u1], "players": ["20"], "metadata": {"team_name": "Theirs"},
             "settings": {"wins": 5, "fpts": 1300, "ppts": 1600}}]


def test_nothing_that_names_a_person_is_kept_and_ids_are_keyed_pseudonyms():
    lg = public.clean_league(_league("L1", "2025"))
    assert "name" not in lg and "avatar" not in lg and "metadata" not in lg
    assert not any(k.startswith("last_") for k in lg) and "commissioner_note" not in lg["settings"]
    assert lg["settings"]["type"] == 2 and lg["scoring_settings"] == {"pass_td": 4}
    ro = public.clean_rosters(_rosters("111", "222"), KEY)
    assert all("metadata" not in r for r in ro) and ro[0]["settings"]["ppts"] == 1700
    assert ro[0]["owner"] == public.pseudonym("111", KEY) == ro[1]["co_owners"][0] != "111"
    assert public.pseudonym("111", b"j" * 32) != ro[0]["owner"] and public.pseudonym("0", KEY) is None
    tx = public.clean_transactions([{"type": "trade", "roster_ids": [1, 2], "creator": "111",
                                     "metadata": {"notes": "lol"}, "draft_picks": [{"season": "2026", "round": 1}]}], KEY)
    assert tx[0]["creator"] == public.pseudonym("111", KEY) and "metadata" not in tx[0]
    assert tx[0]["draft_picks"][0]["round"] == 1


def test_the_crawl_keeps_long_dynasty_chains_only_writes_no_raw_ids_and_resumes(tmp_path):
    api = {
        "league/START/rosters": _rosters("111", "222"),
        "user/111/leagues/nfl/2025": [_league("D4", "2025"), _league("R1", "2025", typ=0)],
        "user/222/leagues/nfl/2025": [_league("D2", "2025")],
        "league/D4": _league("D4", "2025", prev="D3"), "league/D3": _league("D3", "2024", prev="D2x"),
        "league/D2x": _league("D2x", "2023", prev="D1x"), "league/D1x": _league("D1x", "2022"),
        "league/D2": _league("D2", "2025", prev="D1"), "league/D1": _league("D1", "2024"),
        "league/D4/rosters": _rosters("111", "333"), "league/D2/rosters": _rosters("222", "444"),
        "user/333/leagues/nfl/2025": [], "user/444/leagues/nfl/2025": [],
    }
    for lid in ("D4", "D3", "D2x", "D1x"):
        api[f"league/{lid}/rosters"] = api.get(f"league/{lid}/rosters", _rosters("111", "333"))
        api[f"league/{lid}/winners_bracket"] = [{"r": 1, "m": 1, "t1": 1, "t2": 2, "w": 1, "l": 2}]
        for wk in range(1, 4):
            api[f"league/{lid}/transactions/{wk}"] = [{"type": "trade", "roster_ids": [1, 2], "creator": "111"}]
    calls = []

    def get(path):
        calls.append(path)
        return api.get(path)
    cr = public.Crawler(get, out=tmp_path, min_seasons=4, max_chains=10, max_weeks=3, key=KEY, log=lambda *_: None)
    res = cr.run("START")
    assert res["chains"] == 1 and res["seasons"] == 4                 # D4's chain; D2 too short; R1 not dynasty
    files = sorted(p.name for p in (tmp_path / "leagues").glob("*.json"))
    assert files == ["D1x.json", "D2x.json", "D3.json", "D4.json"]
    season = json.loads((tmp_path / "leagues" / "D4.json").read_text())
    assert len(season["transactions"]) == 3 and season["winners_bracket"][0]["w"] == 1
    blob = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    for raw in ("111", "222", "333", "444", "Secret", "somebody", "Mine", "Theirs"):
        assert raw not in blob, raw
    n = len(calls)
    public.Crawler(get, out=tmp_path, min_seasons=4, max_chains=10, max_weeks=3, key=KEY,
                   log=lambda *_: None).run("START")
    assert len(calls) == n                                            # nothing fetched twice
