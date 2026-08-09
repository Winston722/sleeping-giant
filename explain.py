"""The insights layer: turn DAVE's knowledge artifact into words.

DAVE ships the decomposition — one JSONL record per board row carrying the
year-by-year DCF ledger that produced its number — and deliberately ships no
prose. This module is the other half of that bargain: it reads
output/player_explanations.jsonl and writes the interpretation.

The contract (dave-ledger docs/contract.md#player-explanations) is not just a
schema; it constrains what an interpreter may SAY, and those constraints are
enforced here rather than remembered:

  IDENTITY FIRST. The context record names the board it explains
  (artifact_id, board_sha256) and the metadata sidecar names the JSONL
  (explanations.sha256). Nothing is interpreted until
  context.board_sha256 == meta.board_sha256 == sha256(draft_board.csv) and
  meta.explanations.sha256 == sha256(player_explanations.jsonl). The four
  output files are renamed individually, so cross-file consistency is a
  checkable identity, not an assumption.

  WORDS THAT ARE FORBIDDEN BY THE SEMANTICS.
  - hit_probability is P(any early-career production at all) — ~0.99 for
    top picks by construction. It is never rendered as a success chance.
  - On a prior-basis rookie, availability_score carries the conditional
    schedule share; the word "availability" is never used for those rows.
  - talent_ppg is never compared across state_semantics populations; each
    row's own ledger is the only currency converter.
  - Expectation is not confidence: a heavy value tail is a small
    probability of sustained elite production contributing expectation,
    never "the model is confident he lasts". DAVE exposes no calibrated
    outcome distribution, so no confidence language exists to borrow.
  - The board is risk-neutral unless context.risk_discount says otherwise;
    volatility is reported as unpriced information, not as a deduction.

Two outputs per player: a structured brief (the facts an LLM or a template
needs, already interpreted into drivers) and deterministic prose rendered
from it. --prompt emits the briefs wrapped in the conventions, ready to hand
to a model that writes better sentences than a template does.

Usage:
    python explain.py "Josh Allen" "Brock Bowers"
    python explain.py --top 10
    python explain.py --top 5 --prompt
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAVE_CANDIDATES = (HERE.parent / "workspace" / "dave-ledger",
                   Path("/workspace/dave-ledger"))


def find_dave_root():
    for candidate in DAVE_CANDIDATES:
        if (candidate / "output").is_dir():
            return candidate
    raise SystemExit("dave-ledger output not found; run its pipeline first.")


def load_verified(dave_root):
    """(context, records) — but only after the generation identity closes.

    A stale or mixed generation would let this module confidently explain
    numbers the board does not show, which is worse than failing.
    """
    out = dave_root / "output"
    board_bytes = (out / "draft_board.csv").read_bytes()
    explain_bytes = (out / "player_explanations.jsonl").read_bytes()
    meta = json.loads((out / "draft_board.meta.json").read_text())

    lines = explain_bytes.decode().splitlines()
    context = json.loads(lines[0])
    if context.get("record") != "context":
        raise SystemExit("Artifact malformed: first record is not context.")

    board_sha = hashlib.sha256(board_bytes).hexdigest()
    checks = {
        "context.board_sha256 == sha256(draft_board.csv)":
            context.get("board_sha256") == board_sha,
        "meta.board_sha256 == sha256(draft_board.csv)":
            meta.get("board_sha256") == board_sha,
        "context.artifact_id == meta.artifact_id":
            context.get("artifact_id") == meta.get("artifact_id"),
        "meta.explanations.sha256 == sha256(player_explanations.jsonl)":
            (meta.get("explanations") or {}).get("sha256")
            == hashlib.sha256(explain_bytes).hexdigest(),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SystemExit(
            "Generation identity failed — refusing to interpret a mixed or "
            "stale generation:\n  " + "\n  ".join(failed))

    records = [json.loads(line) for line in lines[1:]]
    return context, records


def _pct(x):
    return f"{x:.0%}"


def brief(record, context):
    """The interpreted facts: what drives this number, in plain fields.

    Everything here is derived from the record alone — drivers, timing, and
    population-appropriate vocabulary — so a renderer (template or LLM)
    never has to touch the raw semantics and cannot misuse them.
    """
    state = record["state"]
    headline = record["headline"]
    projection = record["projection"]
    aggregates = record["aggregates"]
    group = record["fantasy_group"]
    floor = record["replacement"]["floor_ppg"]
    is_prior = state["state_semantics"] == "rookie_calibrated_conditional_talent"

    total_vorp = sum(y["pv_player"] - y["pv_replacement"] for y in projection)
    early_vorp = sum(y["pv_player"] - y["pv_replacement"]
                     for y in projection if y["year"] <= 3)
    win_now_share = early_vorp / total_vorp if total_vorp > 0 else 0.0

    out = {
        "name": record["full_name"],
        "group": group,
        "age": record["current_age"],
        "vorp": headline["vorp"],
        "rank_overall": headline["rank_overall"],
        "rank_position": f"{group}{headline['rank_position']}",
        "population": ("rookie_prior" if is_prior else
                       "blended" if record["value_basis"] == "blended"
                       else "observed"),
        "projected_years": aggregates["projected_years"],
        "win_now_share": win_now_share,
        "value_timing": ("win-now" if win_now_share >= 0.75 else
                         "balanced" if win_now_share >= 0.5 else
                         "duration-heavy"),
        "volatility_note": (
            f"performance CV {state['performance_cv']:.2f}, unpriced "
            "(risk-neutral board)"
            if not context.get("risk_discount") else None),
    }

    if is_prior:
        rookie = record["rookie"]
        out["rookie"] = {
            "pick": rookie["pick"],
            "draft_class": rookie["draft_class"],
            # Contract: conditional schedule share, never "availability".
            "conditional_schedule_share": rookie["share_if_hit"],
            "slot_level_ppg": rookie["conditional_prior"],
            "consensus_adjustment": rookie.get("ecr_adjustment"),
            "landing_spot_multiplier": rookie["opportunity_multiplier"],
            "year_one_calibrator": rookie["level_calibrator"],
            "scarce_fraction": (
                1.0 - (sum(y["pv_replacement"] for y in projection)
                       / sum(y["pv_player"] for y in projection))
                if record["mechanics"].get("rookie_replacement_pricing")
                == "surplus_share" else None),
        }
    else:
        out["veteran"] = {
            "talent_ppg": state["talent_ppg"],
            "floor_ppg": floor,
            "margin_ppg": state["talent_ppg"] - floor,
            "availability": state["availability_score"],
            "prior_weight": state["prior_weight"],
            "signal_blend_weight": state["signal_blend_weight"],
        }
    return out


def render(b):
    """Deterministic prose from a brief. Short on purpose: the template's
    job is to be correct; a language model's job is to be eloquent."""
    lines = []
    head = (f"{b['name']} ({b['group']}, {b['age']:.0f}) — "
            f"#{b['rank_overall']} overall, {b['rank_position']}, "
            f"VORP {b['vorp']:.0f}.")
    lines.append(head)

    if b["population"] == "rookie_prior":
        rk = b["rookie"]
        parts = [f"Priced entirely from draft capital: pick "
                 f"{rk['pick']:.0f} of the {rk['draft_class']:.0f} class"]
        adj = rk.get("consensus_adjustment")
        if adj and abs(adj - 1) >= 0.05:
            parts.append(f"consensus ranks him {_pct(abs(adj - 1))} "
                         f"{'above' if adj > 1 else 'below'} his slot")
        opp = rk["landing_spot_multiplier"]
        if abs(opp - 1) >= 0.05:
            parts.append(f"his landing spot "
                         f"{'clears' if opp > 1 else 'crowds'} the path "
                         f"({opp:.2f}x)")
        lines.append("; ".join(parts) + ".")
        if rk.get("scarce_fraction") is not None:
            lines.append(
                f"About {_pct(rk['scarce_fraction'])} of his projected "
                "production counts above replacement — the fraction players "
                "with his draft capital historically delivered, not a claim "
                "about his particular outcome.")
        lines.append(
            f"He is projected over {b['projected_years']} seasons with "
            f"{_pct(1 - b['win_now_share'])} of surplus beyond year three. "
            "That tail is expected value from a range of outcomes, including "
            "failure — not a forecast that he plays that long.")
    else:
        vet = b["veteran"]
        lines.append(
            f"Talent {vet['talent_ppg']:.1f} ppg against a "
            f"{vet['floor_ppg']:.1f} replacement floor "
            f"(margin {vet['margin_ppg']:+.1f}), delivering about "
            f"{_pct(vet['availability'])} of a schedule.")
        if vet["prior_weight"] >= 0.3:
            lines.append(
                f"The estimate still leans {_pct(vet['prior_weight'])} on "
                "his prior rather than observed play.")
        timing = {"win-now": "value is concentrated now",
                  "balanced": "value is spread across the window",
                  "duration-heavy": "value rides on the later years"}
        lines.append(
            f"Projected over {b['projected_years']} seasons, "
            f"{_pct(b['win_now_share'])} of surplus inside three years — "
            f"{timing[b['value_timing']]}.")
    if b.get("volatility_note"):
        lines.append(f"Volatility: {b['volatility_note']}.")
    return " ".join(lines)


PROMPT_PREAMBLE = """\
You are writing dynasty fantasy football player interpretations from DAVE's
valuation briefs. Hard rules, from the model's own contract:
- Never describe a rookie's hit probability as a chance of success; it is
  the probability of any production at all and is ~0.99 for top picks.
- Never say the model is "confident" about career length; long tails are
  expected value over a range of outcomes, including failure.
- Never compare talent numbers between rookies and veterans; ranks and VORP
  are the only shared currency.
- For rookies, say "conditional schedule share", never "availability".
- The board is risk-neutral: volatility is information for the reader, not
  a deduction already taken.
Write 3-5 sentences per player, plain and specific, from the brief alone.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="player names to explain")
    parser.add_argument("--top", type=int, help="explain the top N by VORP")
    parser.add_argument("--prompt", action="store_true",
                        help="emit an LLM-ready prompt instead of prose")
    args = parser.parse_args()

    context, records = load_verified(find_dave_root())
    by_rank = sorted(records, key=lambda r: r["headline"]["rank_overall"])

    chosen = []
    if args.top:
        chosen = by_rank[:args.top]
    for name in args.names:
        matches = [r for r in records
                   if r["full_name"].lower() == name.lower()]
        if not matches:
            print(f"(no record for '{name}')", file=sys.stderr)
        chosen += matches
    if not chosen:
        parser.error("give player names or --top N")

    briefs = [brief(r, context) for r in chosen]
    if args.prompt:
        print(PROMPT_PREAMBLE)
        print(f"League context: {context['num_teams']} teams, "
              f"replacement basis {context['replacement_basis']}, "
              f"season progress {context['season_progress']:.0%}, "
              f"model {context['model_version']}.")
        print(json.dumps(briefs, indent=2))
    else:
        for b in briefs:
            print(render(b))
            print()


if __name__ == "__main__":
    main()
