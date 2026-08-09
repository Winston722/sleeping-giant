"""The interpreter must refuse mixed generations and honor the contract's
language constraints — the two failure modes that would make it confidently
wrong rather than merely dull."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import explain  # noqa: E402


def _context(board_sha):
    return {
        "record": "context", "artifact_id": "abc123",
        "board_sha256": board_sha, "model_version": "joint_production_v4",
        "current_year": 2025, "season_progress": 1.0, "num_teams": 12,
        "replacement_basis": "lineup_assignment", "discount_rate": 0.15,
        "risk_discount": 0.0,
    }


def _rookie_record():
    projection = [
        {"year": h, "pv_player": 100.0 - 10 * h,
         "pv_replacement": (100.0 - 10 * h) * 0.82} for h in range(1, 9)
    ]
    return {
        "record": "player", "player_id": "rk", "full_name": "Test Rookie",
        "fantasy_group": "QB", "position": "QB", "current_age": 22.0,
        "value_basis": "prior",
        "headline": {"vorp": 120.0, "dcf_value": 500.0,
                     "replacement_value": 380.0, "rank_overall": 3,
                     "rank_position": 1},
        "state": {"state_semantics": "rookie_calibrated_conditional_talent",
                  "talent_ppg": 18.0, "availability_score": 0.7,
                  "performance_cv": 0.8, "prior_weight": 1.0,
                  "signal_blend_weight": 1.0},
        "replacement": {"floor_ppg": 17.9, "basis": "lineup_assignment"},
        "risk": {"risk_penalty": 0.0, "effective_risk": 1.2},
        "mechanics": {"survival_model": "marginal_profile",
                      "rookie_replacement_pricing": "surplus_share"},
        "rookie": {"pick": 1.0, "draft_class": 2026.0,
                   "hit_probability": 0.998, "conditional_prior": 19.0,
                   "share_if_hit": 0.7, "level_calibrator": 1.05,
                   "opportunity_multiplier": 1.0, "prior_ppg": 19.0,
                   "ecr_adjustment": 0.93, "p_hit": 0.998},
        "aggregates": {"projected_years": 8, "pv_share_year0": 0.0,
                       "pv_share_years_1_3": 0.55},
        "projection": projection,
        "totals": {"dcf_value": 500.0, "replacement_value": 380.0,
                   "vorp": 120.0},
    }


def _veteran_record():
    rec = _rookie_record()
    rec.update({
        "player_id": "vet", "full_name": "Test Veteran",
        "value_basis": "observed", "rookie": None,
        "headline": {**rec["headline"], "rank_overall": 4,
                     "rank_position": 2},
    })
    rec["state"] = {"state_semantics": "observed_blended_talent",
                    "talent_ppg": 21.0, "availability_score": 0.85,
                    "performance_cv": 0.4, "prior_weight": 0.1,
                    "signal_blend_weight": 1.0}
    rec["mechanics"] = {"survival_model": "participation",
                        "rookie_replacement_pricing": None}
    return rec


def _write_generation(root, tamper=None):
    out = root / "output"
    out.mkdir()
    board = b"player_id,vorp\nrk,120.0\nvet,110.0\n"
    (out / "draft_board.csv").write_bytes(board)
    board_sha = hashlib.sha256(board).hexdigest()
    lines = [json.dumps(_context(board_sha)),
             json.dumps(_rookie_record()), json.dumps(_veteran_record())]
    explain_bytes = ("\n".join(lines) + "\n").encode()
    (out / "player_explanations.jsonl").write_bytes(explain_bytes)
    meta = {"artifact_id": "abc123", "board_sha256": board_sha,
            "explanations": {
                "sha256": hashlib.sha256(explain_bytes).hexdigest()}}
    if tamper:
        meta.update(tamper)
    (out / "draft_board.meta.json").write_text(json.dumps(meta))
    return root


def test_loads_only_when_the_generation_identity_closes(tmp_path):
    context, records = explain.load_verified(_write_generation(tmp_path))
    assert context["artifact_id"] == "abc123"
    assert len(records) == 2


def test_refuses_a_mixed_generation(tmp_path):
    root = _write_generation(tmp_path, tamper={"board_sha256": "deadbeef"})
    with pytest.raises(SystemExit) as failure:
        explain.load_verified(root)
    assert "meta.board_sha256" in str(failure.value)


def test_rookie_language_honours_the_contract(tmp_path):
    context, records = explain.load_verified(_write_generation(tmp_path))
    rookie = next(r for r in records if r["value_basis"] == "prior")
    b = explain.brief(rookie, context)
    prose = explain.render(b)

    # Share-if-hit ships under its true name, never as availability.
    assert "conditional_schedule_share" in b["rookie"]
    assert "availability" not in prose.lower()
    # Expectation is not confidence, and the hurdle is never a success chance.
    assert "not a forecast" in prose
    for forbidden in ("confident", "chance of hitting", "99.8%"):
        assert forbidden not in prose.lower()
    # The scarce fraction is the C14 share, read off the ledger itself.
    assert b["rookie"]["scarce_fraction"] == pytest.approx(0.18, abs=1e-9)


def test_veteran_brief_carries_margin_over_floor(tmp_path):
    context, records = explain.load_verified(_write_generation(tmp_path))
    vet = next(r for r in records if r["value_basis"] == "observed")
    b = explain.brief(vet, context)
    assert b["veteran"]["margin_ppg"] == pytest.approx(21.0 - 17.9)
    prose = explain.render(b)
    assert "replacement floor" in prose
