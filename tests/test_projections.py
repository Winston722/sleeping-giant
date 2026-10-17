"""projections.py: Sleeper's weekly projections for DAVE's lineup study (owner, 2026-10-17).

What must hold: only projected stats are kept (no ranks or draft positions, nothing that is not a number), players
with nothing projected are dropped, both of Sleeper's response shapes are read, and files land under the gitignored
data/projections/."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import projections  # noqa: E402


def test_a_week_keeps_projected_stats_only(monkeypatch):
    def fake(path):
        assert path == "projections/nfl/regular/2024/5"
        return {"10": {"pts_ppr": 9.1, "rec": 3.4, "adp_ppr": 40.0, "pos_rank_ppr": 12, "note": "x", "gp": 1.0},
                "11": {"adp_dd_ppr": 99.0}, "12": {}}
    monkeypatch.setattr(projections, "_get", fake)
    assert projections.week(2024, 5) == {"10": {"pts_ppr": 9.1, "rec": 3.4, "gp": 1.0}}


def test_the_list_shape_reads_the_same(monkeypatch):
    monkeypatch.setattr(projections, "_get", lambda p: [{"player_id": "7", "stats": {"idp_tkl_solo": 4.2, "rank_std": 3}},
                                                        {"player_id": None, "stats": {"rec": 1.0}}])
    assert projections.week(2025, 1) == {"7": {"idp_tkl_solo": 4.2}}


def test_main_writes_one_file_a_week_and_is_gitignored(monkeypatch, tmp_path):
    monkeypatch.setattr(projections, "_get", lambda p: {"1": {"rec": 1.0}} if p.endswith("/1") else {})
    monkeypatch.setattr(projections, "PACE", 0.0)
    assert projections.main(["--seasons", "2024", "--weeks", "1-2", "--out", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "2024" / "1.json").read_text()) == {"1": {"rec": 1.0}}
    assert not (tmp_path / "2024" / "2.json").exists()                  # nothing projected: no file
    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text()
    assert "data/projections/" in ignore
