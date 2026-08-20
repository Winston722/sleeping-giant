"""The insights layer: serve the words DAVE already wrote.

DAVE ships two artifacts per generation — `player_explanations.jsonl`, the
year-by-year DCF ledger behind every board row, and `player_briefs.jsonl`,
DAVE-ID's interpretation of it: a structured brief plus deterministic prose
per player. **This module renders the second one. It does not derive it.**

IT USED TO DERIVE IT, AND THAT WAS THE BUG. The vocabulary rules — a
rookie's schedule share is not availability, a hit probability is not a
success chance, an expectation is not confidence, volatility on a
risk-neutral board is unpriced information rather than a deduction already
taken — are semantic claims about DAVE's own state. Enforcing them here put
them one repository away from the semantics they constrain and from
`docs/contract.md`, which defines them, guarded by a docstring rather than a
suite. A semantic could change in DAVE and these rules would go stale in
silence.

They now live in `dave_ledger/analysis/prose.py` with tests beside the
contract. Moving them found a live defect this file had been carrying: it
formatted `performance_cv` unconditionally and raised TypeError on the 26
of 2,083 board rows that carry no CV — none ranked above #452, which is why
`--top 10` never reached one.

What stays here is the consumer's own job, and it is not nothing:

  IDENTITY FIRST. The four artifacts are renamed individually, so a stale
  or mixed generation would let this module confidently serve prose about
  numbers the board does not show. Nothing is rendered until
  context.board_sha256 == meta.board_sha256 == sha256(draft_board.csv),
  meta.explanations.sha256 == sha256(player_explanations.jsonl), AND
  meta.briefs.sha256 == sha256(player_briefs.jsonl). The briefs join the
  same chain rather than being trusted for arriving in the same directory.

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
    """(context, records, briefs_by_id, meta) — after the generation closes.

    A stale or mixed generation would let this module confidently serve
    prose about numbers the board does not show, which is worse than
    failing. The briefs artifact is verified on the SAME chain: arriving in
    the same directory is not evidence it belongs to the same generation.
    """
    out = dave_root / "output"
    board_bytes = (out / "draft_board.csv").read_bytes()
    explain_bytes = (out / "player_explanations.jsonl").read_bytes()
    meta = json.loads((out / "draft_board.meta.json").read_text())

    brief_path = out / "player_briefs.jsonl"
    if not brief_path.exists():
        raise SystemExit(
            "No player_briefs.jsonl in DAVE's output. This board predates "
            "DAVE-ID's prose artifact — re-run DAVE's pipeline to publish "
            "it.\n\nThis module renders the briefs DAVE writes; it no "
            "longer derives its own, because deriving them here put the "
            "contract's vocabulary rules a repository away from the "
            "semantics they constrain.")
    brief_bytes = brief_path.read_bytes()

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
        "meta.briefs.sha256 == sha256(player_briefs.jsonl)":
            (meta.get("briefs") or {}).get("sha256")
            == hashlib.sha256(brief_bytes).hexdigest(),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SystemExit(
            "Generation identity failed — refusing to interpret a mixed or "
            "stale generation:\n  " + "\n  ".join(failed))

    records = [json.loads(line) for line in lines[1:]]
    briefs = {}
    for line in brief_bytes.decode().splitlines():
        entry = json.loads(line)
        briefs[entry["player_id"]] = entry

    missing = [r["player_id"] for r in records
               if r["player_id"] not in briefs]
    if missing:
        raise SystemExit(
            f"{len(missing)} explained players have no brief "
            f"(first: {missing[0]}). The two artifacts disagree about who "
            "is on this board; refusing to serve a partial reading.")
    return context, records, briefs, meta


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="player names to explain")
    parser.add_argument("--top", type=int, help="explain the top N by VORP")
    parser.add_argument("--prompt", action="store_true",
                        help="emit an LLM-ready prompt instead of prose")
    args = parser.parse_args()

    context, records, briefs, meta = load_verified(find_dave_root())
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

    entries = [briefs[r["player_id"]] for r in chosen]
    if args.prompt:
        # The preamble ships INSIDE the artifact's own generation, so the
        # rules handed to a language model are DAVE's current ones rather
        # than a copy that drifted from them.
        print(meta["briefs"]["preamble"])
        print(f"League context: {context['num_teams']} teams, "
              f"replacement basis {context['replacement_basis']}, "
              f"season progress {context['season_progress']:.0%}, "
              f"model {context['model_version']}.")
        print(json.dumps([e["brief"] for e in entries], indent=2))
    else:
        for entry in entries:
            print(entry["prose"])
            print()


if __name__ == "__main__":
    main()
