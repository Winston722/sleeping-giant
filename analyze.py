"""
League analysis, through DAVE's own code path.

Every ad-hoc version of this script invented a bug. One resolved Sleeper IDs with
crosswalk.to_gsis instead of league_state.load, so twenty-one rostered players
looked unmatched when the loader resolves all of them. Another read num_teams from
config instead of league state, setting every replacement bar for twelve teams in
an eight-team league. Neither defect was in the engine.

So this calls the same loaders and the same lineup simulator the engine uses, and
adds only the thing DAVE deliberately does not know: who owns whom.

Usage: python analyze.py [--trials 400]
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DAVE = Path(__file__).resolve().parent.parent / "workspace" / "dave-ledger"
for candidate in (DAVE, Path("/workspace/dave-ledger")):
    if (candidate / "src").is_dir():
        sys.path.insert(0, str(candidate / "src"))
        DAVE_ROOT = candidate
        break
else:  # pragma: no cover - only hit when dave-ledger is not checked out alongside
    raise SystemExit("dave-ledger not found; clone it beside this repo.")

from dave_ledger.analysis import lineup  # noqa: E402
from dave_ledger.analysis.valuation import AssetValuator  # noqa: E402
from dave_ledger.core.config import load_config  # noqa: E402
from dave_ledger.core.crosswalk import PlayerCrosswalk  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "raw"
HORIZONS = (0, 1, 3, 5)

# DAVE locates its own config and data by walking up from the working directory,
# so it has to be run from inside its own tree. Every path this script needs is
# already absolute, so moving is free.
os.chdir(DAVE_ROOT)


def load_board():
    board = pd.read_csv(DAVE_ROOT / "output" / "draft_board.csv")
    meta = json.loads((DAVE_ROOT / "output" / "draft_board.meta.json").read_text())
    return board, meta


def load_rosters(crosswalk):
    """Owner -> players, resolved the way league_state resolves them: id then name."""
    rosters = json.loads((RAW / "rosters.json").read_text())
    users = json.loads((RAW / "users.json").read_text())
    names = {u["user_id"]: u.get("display_name") for u in users}
    directory = {
        str(p["id"]): p for p in json.loads(
            (HERE / "data" / "league_state.json").read_text()
        )["rostered_players"]
    }

    entries, owners = [], []
    for roster in rosters:
        owner = names.get(roster.get("owner_id"), f"roster{roster.get('roster_id')}")
        for pid in roster.get("players") or []:
            record = directory.get(str(pid), {})
            entries.append({
                "id": pid,
                "name": record.get("name"),
                "position": record.get("position"),
            })
            owners.append(owner)

    resolved, report = crosswalk.resolve(entries, id_type="sleeper")
    return pd.DataFrame({
        "owner": owners,
        "player_id": resolved,
        "sleeper_name": [e["name"] for e in entries],
    }), report


def age_forward(board, cfg, horizons=HORIZONS):
    """Talent advanced through the same growth and decay curves the DCF uses."""
    valuator = AssetValuator(pd.DataFrame({"season": [cfg["context"]["current_year"]]}), cfg)
    out = board.copy()
    for h in horizons:
        values = []
        for row in board.itertuples():
            ppg, age, exp = row.talent_ppg, row.current_age, row.years_exp
            age = 26.0 if pd.isna(age) else age
            exp = 5.0 if pd.isna(exp) else exp
            peak = valuator.peak_for(row.fantasy_group, ppg)
            for step in range(h):
                ppg = valuator.advance_ppg(row.fantasy_group, age + step, exp + step, ppg, peak)
            values.append(ppg)
        out[f"ppg_{h}"] = values
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=400)
    args = parser.parse_args()

    cfg = load_config()
    board, meta = load_board()
    cfg.setdefault("context", {})["current_year"] = meta["current_year"]
    print(f"Board generated {meta['generated_at'][:10]}, season {meta['current_year']}, "
          f"replacement basis {meta.get('replacement_basis')}\n")

    crosswalk = PlayerCrosswalk.load()
    owned, report = load_rosters(crosswalk)
    unresolved = owned["player_id"].isna().sum()
    print(f"Rostered players: {len(owned)}, unresolved {unresolved}, "
          f"matched by name {len(report.matched_by_name)}")

    aged = age_forward(board, cfg)
    joined = owned.dropna(subset=["player_id"]).merge(aged, on="player_id", how="left")
    missing = joined["vorp"].isna().sum()
    if missing:
        print(f"⚠️ {missing} resolved players are absent from the board:")
        for name in joined.loc[joined["vorp"].isna(), "sleeper_name"]:
            print(f"     {name}")
    joined = joined[joined["vorp"].notna()]

    starters = cfg["league"]["starters"]
    rows = []
    for owner, roster in joined.groupby("owner"):
        record = {"owner": owner, "n": len(roster), "vorp": roster["vorp"].sum()}
        weights = roster["vorp"].clip(lower=0) + 1e-9
        record["age"] = float(np.average(roster["current_age"].fillna(26), weights=weights))
        record["prior_share"] = float(
            np.average(roster["prior_weight"].fillna(1.0), weights=weights)
        )
        for h in HORIZONS:
            sim = lineup.simulate_season(
                roster, starters, metric=f"ppg_{h}", trials=args.trials,
                rng=np.random.default_rng(1234),
            )
            record[f"sim{h}"] = sim["mean"]
            if h == 0:
                record["sim0_p10"], record["sim0_p90"] = sim["p10"], sim["p90"]
        rows.append(record)

    table = pd.DataFrame(rows).set_index("owner")
    table["d3"] = table["sim3"] - table["sim0"]
    table["d5"] = table["sim5"] - table["sim0"]
    pd.set_option("display.width", 220)
    print("\n=== Simulated season points (optimal weekly lineup, availability sampled) ===")
    print(table[["n", "sim0", "sim0_p10", "sim0_p90", "sim1", "sim3", "sim5",
                 "d3", "d5", "vorp", "age", "prior_share"]]
          .sort_values("sim0", ascending=False).round(1).to_string())

    print("\n=== Depth: what the roster beyond the starters is worth ===")
    depth = []
    for owner, roster in joined.groupby("owner"):
        full = table.loc[owner, "sim0"]
        top = roster.nlargest(15, "ppg_0")
        thin = lineup.simulate_season(
            top, starters, metric="ppg_0", trials=args.trials,
            rng=np.random.default_rng(1234),
        )["mean"]
        depth.append({"owner": owner, "full": full, "starters_only": thin,
                      "depth_value": full - thin})
    print(pd.DataFrame(depth).set_index("owner")
          .sort_values("depth_value", ascending=False).round(1).to_string())

    print("\n=== Most valuable players to their own roster (marginal, top 5 each) ===")
    for owner, roster in joined.groupby("owner"):
        marginal = lineup.marginal_values(
            roster, starters, metric="ppg_0", trials=max(args.trials // 2, 100), seed=7,
        )
        top = marginal.nlargest(5)
        labels = ", ".join(
            f"{roster.set_index('player_id').loc[pid, 'full_name']} {value:.0f}"
            for pid, value in top.items()
        )
        print(f"  {owner:<18} {labels}")


if __name__ == "__main__":
    main()
