"""Tests for the Sleeper -> DAVE scoring translation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring import reconcile  # noqa: E402


def test_maps_offense_and_idp_keys_preserving_values():
    settings = {
        "pass_yd": 0.04, "pass_td": 6, "rush_yd": 0.1, "rec": 0.5,
        "idp_tkl_solo": 1.3, "idp_sack": 5.0, "idp_pass_def": 4.0,
    }
    dave, unmapped, ignored = reconcile(settings)

    assert dave["passing_yards"] == 0.04
    assert dave["passing_tds"] == 6
    assert dave["def_tackles_solo"] == 1.3
    assert dave["def_sacks"] == 5.0
    assert dave["def_pass_defended"] == 4.0
    assert unmapped == []


def test_zero_weighted_settings_are_skipped():
    """A stat present but worth 0 points must not enter DAVE's scoring."""
    dave, _, _ = reconcile({"pass_td": 6, "rush_2pt": 0, "idp_safe": 0})
    assert "rushing_2pt_conversions" not in dave
    assert dave == {"passing_tds": 6}


def test_unknown_scored_key_is_reported_not_dropped():
    """A non-zero key we cannot map is a real gap and must surface."""
    dave, unmapped, ignored = reconcile({"pass_td": 6, "some_new_idp_stat": 2.0})
    assert "passing_tds" in dave
    assert any("some_new_idp_stat" in u for u in unmapped)


def test_known_unmodelled_keys_are_separated_from_unmapped():
    """Deliberate omissions are noise if mixed with real gaps; keep them apart."""
    dave, unmapped, ignored = reconcile({"idp_tkl": 0.75, "idp_td": 6})
    assert unmapped == []
    assert len(ignored) == 2
    assert any("idp_tkl" in i for i in ignored)


def test_combined_and_split_tackle_keys_do_not_collide():
    """
    A league scoring split tackles must map cleanly; the combined key is a
    documented non-model, not an accidental overwrite.
    """
    dave, unmapped, ignored = reconcile({
        "idp_tkl_solo": 1.5, "idp_tkl_ast": 0.75, "idp_tkl": 1.0,
    })
    assert dave["def_tackles_solo"] == 1.5
    assert dave["def_tackle_assists"] == 0.75
    assert "def_tackles_with_assist" not in dave  # the combined key is not modelled here
    assert any("idp_tkl=" in i for i in ignored)
