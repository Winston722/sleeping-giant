"""The consumer's job, now that it no longer writes the words.

DAVE-ID owns the prose and its vocabulary rules; those are tested beside
the contract in dave-ledger. What is left here is the failure mode that is
genuinely the consumer's: serving a mixed or stale generation, which would
make this module confidently wrong rather than merely dull.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import explain  # noqa: E402

PREAMBLE = "Hard rules, from the model's own contract: ...\n"


def _context(board_sha):
    return {
        "record": "context", "artifact_id": "abc123",
        "board_sha256": board_sha, "model_version": "joint_production_v4",
        "current_year": 2025, "season_progress": 1.0, "num_teams": 12,
        "replacement_basis": "lineup_assignment", "discount_rate": 0.15,
        "risk_discount": 0.0,
    }


def _record(player_id, name, rank):
    return {"record": "player", "player_id": player_id, "full_name": name,
            "fantasy_group": "QB", "headline": {"rank_overall": rank}}


def _brief(player_id, name, prose):
    return {"record": "brief", "player_id": player_id,
            "brief": {"name": name, "population": "observed"},
            "prose": prose}


def _write_generation(root, tamper=None, briefs=None, omit_briefs=False):
    out = root / "output"
    out.mkdir()
    board = b"player_id,vorp\nrk,120.0\nvet,110.0\n"
    (out / "draft_board.csv").write_bytes(board)
    board_sha = hashlib.sha256(board).hexdigest()

    lines = [json.dumps(_context(board_sha)),
             json.dumps(_record("rk", "Test Rookie", 3)),
             json.dumps(_record("vet", "Test Veteran", 4))]
    explain_bytes = ("\n".join(lines) + "\n").encode()
    (out / "player_explanations.jsonl").write_bytes(explain_bytes)

    meta = {"artifact_id": "abc123", "board_sha256": board_sha,
            "explanations": {
                "sha256": hashlib.sha256(explain_bytes).hexdigest()}}

    if not omit_briefs:
        if briefs is None:
            briefs = [_brief("rk", "Test Rookie", "Rookie prose."),
                      _brief("vet", "Test Veteran", "Veteran prose.")]
        brief_bytes = ("\n".join(json.dumps(b) for b in briefs)
                       + "\n").encode()
        (out / "player_briefs.jsonl").write_bytes(brief_bytes)
        meta["briefs"] = {
            "sha256": hashlib.sha256(brief_bytes).hexdigest(),
            "preamble": PREAMBLE}

    if tamper:
        meta.update(tamper)
    (out / "draft_board.meta.json").write_text(json.dumps(meta))
    return root


def test_loads_only_when_the_generation_identity_closes(tmp_path):
    context, records, briefs, meta = explain.load_verified(
        _write_generation(tmp_path))
    assert context["artifact_id"] == "abc123"
    assert len(records) == 2
    assert set(briefs) == {"rk", "vet"}
    assert meta["briefs"]["preamble"] == PREAMBLE


def test_refuses_a_mixed_generation(tmp_path):
    root = _write_generation(tmp_path, tamper={"board_sha256": "deadbeef"})
    with pytest.raises(SystemExit, match="Generation identity failed"):
        explain.load_verified(root)


def test_briefs_join_the_identity_chain(tmp_path):
    """Arriving in the same directory is not evidence of the same
    generation. A briefs file from a previous run must be caught."""
    root = _write_generation(tmp_path)
    (root / "output" / "player_briefs.jsonl").write_bytes(
        b'{"record": "brief", "player_id": "rk", "prose": "stale"}\n')
    with pytest.raises(SystemExit, match="meta.briefs.sha256"):
        explain.load_verified(root)


def test_missing_briefs_artifact_says_what_to_do(tmp_path):
    """A board predating the artifact is a real state, and the message has
    to name the fix rather than just the failure."""
    root = _write_generation(tmp_path, omit_briefs=True)
    with pytest.raises(SystemExit, match="re-run DAVE's pipeline"):
        explain.load_verified(root)


def test_partial_briefs_are_refused_rather_than_served(tmp_path):
    """If the two artifacts disagree about who is on the board, serving the
    intersection would quietly drop players a reader asked about."""
    root = _write_generation(
        tmp_path, briefs=[_brief("rk", "Test Rookie", "Rookie prose.")])
    with pytest.raises(SystemExit, match="have no brief"):
        explain.load_verified(root)


def test_this_module_no_longer_derives_prose(tmp_path):
    """The migration is only real if the old path is gone. A consumer that
    kept a private renderer would drift from DAVE's the moment a semantic
    changed — which is the whole reason the prose moved."""
    source = (Path(__file__).resolve().parents[1] / "explain.py").read_text()
    for gone in ("def brief(", "def render(", "PROMPT_PREAMBLE ="):
        assert gone not in source, f"{gone!r} still derives prose locally"


def test_renders_the_artifact_verbatim(tmp_path, capsys, monkeypatch):
    root = _write_generation(tmp_path)
    monkeypatch.setattr(explain, "find_dave_root", lambda: root)
    monkeypatch.setattr(sys, "argv", ["explain.py", "--top", "2"])
    explain.main()
    out = capsys.readouterr().out
    assert "Rookie prose." in out and "Veteran prose." in out


def test_prompt_mode_uses_the_generation_s_own_preamble(
        tmp_path, capsys, monkeypatch):
    root = _write_generation(tmp_path)
    monkeypatch.setattr(explain, "find_dave_root", lambda: root)
    monkeypatch.setattr(sys, "argv", ["explain.py", "--top", "1", "--prompt"])
    explain.main()
    out = capsys.readouterr().out
    assert PREAMBLE.strip() in out
    assert "12 teams" in out
