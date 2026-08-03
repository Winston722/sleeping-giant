#!/usr/bin/env python3
"""
Translate a Sleeper league's scoring into DAVE's scoring config.

DAVE values players under whatever scoring rules it is given, keyed by nflverse
stat column names. A league's rules live in Sleeper under different key names.
Rather than hand-maintaining DAVE's config to match a league — the kind of guess
that silently drifts — this reads the league's own scoring_settings and emits the
matching DAVE config, so the real rules drive the valuation.

Two outputs:
  - a reconciliation report (what mapped, what did not, with values)
  - dave_scoring.yaml, a DAVE scoring block ready to drop into a config

Sleeper key names are stable but not exhaustively documented, so any setting we
cannot map is reported loudly rather than dropped — an unmapped scored stat is a
real gap between what the league rewards and what DAVE counts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent

# Sleeper scoring key -> DAVE (nflverse) scoring key.
# Offense first, then IDP. Values are points-per-unit in both systems, so the
# number carries over unchanged; only the key is translated.
SLEEPER_TO_DAVE = {
    # passing
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "pass_int": "passing_interceptions",
    "pass_2pt": "passing_2pt_conversions",
    # rushing
    "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds",
    "rush_2pt": "rushing_2pt_conversions",
    # receiving
    "rec": "receptions",
    "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds",
    "rec_2pt": "receiving_2pt_conversions",
    # fumbles
    "fum_lost": "fumbles_lost_total",
    # IDP — tackles
    "idp_tkl_solo": "def_tackles_solo",
    "idp_tkl_ast": "def_tackle_assists",
    "idp_tkl_loss": "def_tackles_for_loss",
    # IDP — pass rush
    "idp_sack": "def_sacks",
    "idp_qb_hit": "def_qb_hits",
    # IDP — coverage
    "idp_int": "def_interceptions",
    "idp_pass_def": "def_pass_defended",
    "idp_pass_defended": "def_pass_defended",
    "idp_pd": "def_pass_defended",
    # IDP — takeaways
    "idp_ff": "def_fumbles_forced",
}

# Sleeper keys we knowingly do not carry into DAVE, with why — so they are not
# mistaken for the loud "unmapped" warnings that signal a real gap.
KNOWN_UNMODELLED = {
    "idp_tkl": "combined tackles; DAVE scores solo and assist separately",
    "idp_fum_rec": "fumble recovery: opportunity-driven expectation not modelled; scored on actuals only",
    "idp_safe": "safety: too rare to model",
    "idp_td": "defensive TD: too rare to model",
    "idp_blk_kick": "blocked kick: too rare to model",
    "bonus_rec_te": "positional reception bonus; needs per-position scoring, not yet supported",
    "def_st_td": "team-defense / special-teams scoring; DAVE values individual players",
}


def reconcile(scoring_settings: Dict[str, float]) -> Tuple[Dict[str, float], List[str], List[str]]:
    """
    Map a Sleeper scoring_settings dict to a DAVE scoring dict.

    Returns (dave_scoring, unmapped, ignored):
      dave_scoring  keys DAVE understands, with the league's point values
      unmapped      Sleeper keys with a non-zero value that we could not map and
                    do not recognise — each a potential correctness gap
      ignored       non-zero Sleeper keys deliberately not modelled (with reasons)
    """
    dave: Dict[str, float] = {}
    unmapped: List[str] = []
    ignored: List[str] = []

    for key, value in scoring_settings.items():
        if not value:
            continue  # zero-weighted settings do not affect scoring
        if key in SLEEPER_TO_DAVE:
            dave[SLEEPER_TO_DAVE[key]] = value
        elif key in KNOWN_UNMODELLED:
            ignored.append(f"{key}={value}  ({KNOWN_UNMODELLED[key]})")
        else:
            unmapped.append(f"{key}={value}")

    return dave, sorted(unmapped), sorted(ignored)


def _as_yaml(dave_scoring: Dict[str, float]) -> str:
    """Emit a DAVE scoring block without a YAML dependency."""
    lines = ["# Generated from the league's Sleeper scoring_settings by scoring.py.", "scoring:"]
    for key in sorted(dave_scoring):
        lines.append(f"  {key}: {dave_scoring[key]}")
    return "\n".join(lines) + "\n"


def main() -> None:
    league_path = ROOT / "data" / "raw" / "league.json"
    if not league_path.exists():
        raise SystemExit(f"{league_path} not found — run sync.py (in CI) first.")

    league = json.loads(league_path.read_text())
    scoring_settings = league.get("scoring_settings") or {}
    dave, unmapped, ignored = reconcile(scoring_settings)

    print(f"Sleeper scoring settings: {len(scoring_settings)}  ->  DAVE scored rules: {len(dave)}")
    if ignored:
        print("\nDeliberately not modelled:")
        for line in ignored:
            print(f"  - {line}")
    if unmapped:
        print("\n⚠️  UNMAPPED non-zero settings (a gap between league scoring and DAVE):")
        for line in unmapped:
            print(f"  - {line}")
        print("  Extend SLEEPER_TO_DAVE or KNOWN_UNMODELLED to resolve each.")

    out = ROOT / "data" / "dave_scoring.yaml"
    out.write_text(_as_yaml(dave))
    print(f"\nWrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
