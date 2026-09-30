"""
League analysis from DAVE's published board.

Reads the board and its sidecar (DAVE's contract, v5 since 2026-09-29) and adds
only what DAVE deliberately does not know from its own output: who owns whom
right now, from this repo's Sleeper capture. It imports nothing from DAVE —
the contract is the interface — and needs only the standard library.

Players join on `sleeper_id`, a board column, so there is no name matching and
no crosswalk here. A player eligible at two groups has two board rows; the player's
headline value is the better row, and a lineup may use either group.

Usage:
    python analyze.py [--board PATH]   # default: ../dave-ledger/output/draft_board.csv

Before anything is printed the board is checked against its sidecar
(`board_sha256`), so a mixed or half-written generation is refused.
"""

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "raw"
BOARD_CANDIDATES = (
    HERE.parent / "dave-ledger" / "output" / "draft_board.csv",
    HERE.parent / "workspace" / "dave-ledger" / "output" / "draft_board.csv",
    Path("/workspace/dave-ledger/output/draft_board.csv"),
)
REQUIRED = ("player_id", "full_name", "fantasy_group", "is_rookie", "value", "exp_s1", "exp_s2",
            "exp_s3", "age", "sleeper_id")
NUMERIC = ("value", "exp_s1", "exp_s2", "exp_s3", "exp_3yr", "age", "championship_value_own_team")
# Which board groups may fill each Sleeper starting slot. Slots not listed (BN,
# IR, TAXI) do not start; a starting slot not listed here (K, DEF) has no board
# group and is reported as unfilled.
SLOT_GROUPS = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
    "DL": {"DL"}, "LB": {"LB"}, "DB": {"DB"},
    "FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"}, "WRRB_FLEX": {"WR", "RB"}, "IDP_FLEX": {"DL", "LB", "DB"},
}
NON_STARTING = {"BN", "IR", "TAXI"}


def find_board(path=None):
    if path:
        return Path(path)
    for candidate in BOARD_CANDIDATES:
        if candidate.exists():
            return candidate
    raise SystemExit("no draft_board.csv found; pass --board, or check out dave-ledger beside this repo "
                     "and build its board (scripts/produce_v5_live_board.py)")


def load_board(board_path):
    """Rows of the published board, after checking the sidecar's digest."""
    board_path = Path(board_path)
    meta_path = board_path.with_name("draft_board.meta.json")
    meta = json.loads(meta_path.read_text())
    digest = hashlib.sha256(board_path.read_bytes()).hexdigest()
    if digest != meta.get("board_sha256"):
        raise SystemExit(f"{board_path} does not match its sidecar's board_sha256: a mixed or partial "
                         "generation. Re-read after the producer finishes, or rebuild.")
    with board_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"board has no {missing}: model_version {meta.get('model_version')!r} "
                             "predates the v5 contract this script reads")
        rows = []
        for row in reader:
            for col in NUMERIC:
                if col in row:
                    row[col] = float(row[col]) if row[col] not in ("", None) else None
            row["is_rookie"] = str(row["is_rookie"]).lower() == "true"
            rows.append(row)
    return rows, meta


def by_sleeper_id(rows):
    """sleeper_id -> {group: row}; a multi-group player keeps one row per group."""
    out = {}
    for row in rows:
        sid = row.get("sleeper_id")
        if sid:
            out.setdefault(str(sid), {})[row["fantasy_group"]] = row
    return out


def best_row(groups):
    return max(groups.values(), key=lambda r: (r["value"] is not None, r["value"] or 0.0))


def lineup(players, slots, metric):
    """Fill the starting slots greedily from the most restrictive slot out.

    `players` maps an id to {group: row}; each player starts at most once, in
    any group the player is eligible for. Values are expected season totals, so this
    is the expected points of the best season-long lineup on expectations: it
    credits no bench cover for injuries and byes. Returns (total, picks,
    unfilled slots)."""
    order = sorted((s for s in slots if s not in NON_STARTING),
                   key=lambda s: len(SLOT_GROUPS.get(s, ())))
    used, picks, unfilled, total = set(), [], [], 0.0
    for slot in order:
        eligible = SLOT_GROUPS.get(slot)
        best = None
        for pid, groups in players.items():
            if pid in used or not eligible:
                continue
            for group, row in groups.items():
                points = row.get(metric)
                if group in eligible and points is not None and (best is None or points > best[0]):
                    best = (points, pid, row)
        if best is None:
            unfilled.append(slot)
            continue
        points, pid, row = best
        used.add(pid)
        total += points
        picks.append((slot, row["full_name"], row["fantasy_group"], points))
    return total, picks, unfilled


def load_league():
    league = json.loads((RAW / "league.json").read_text())
    rosters = json.loads((RAW / "rosters.json").read_text())
    users = {u["user_id"]: u.get("display_name") for u in json.loads((RAW / "users.json").read_text())}
    names = {}
    state_path = HERE / "data" / "league_state.json"
    if state_path.exists():
        names = {str(p["id"]): p.get("name") for p in json.loads(state_path.read_text()).get("rostered_players", [])}
    return league, rosters, users, names


def team_rows(board_ids, league, rosters, users):
    """One summary per team, plus the rostered ids the board does not carry."""
    slots = league["roster_positions"]
    summaries, absent = [], []
    for roster in rosters:
        owner = users.get(roster.get("owner_id")) or f"roster{roster.get('roster_id')}"
        ids = [str(p) for p in roster.get("players") or []]
        out_now = {str(p) for p in (roster.get("reserve") or []) + (roster.get("taxi") or [])}
        on_board = {pid: board_ids[pid] for pid in ids if pid in board_ids}
        absent += [(owner, pid) for pid in ids if pid not in board_ids]
        best = {pid: best_row(groups) for pid, groups in on_board.items()}
        values = [r["value"] for r in best.values() if r["value"] is not None]
        positive = sum(v for v in values if v > 0)
        weights = [(max(r["value"] or 0.0, 0.0), r) for r in best.values()]
        wsum = sum(w for w, _ in weights) or 1.0
        age = sum(w * (r["age"] if r["age"] is not None else 26.0) for w, r in weights) / wsum
        rookie_share = sum(w for w, r in weights if r["is_rookie"]) / wsum
        # This season, IR and taxi players cannot start (DAVE's title and trade
        # values make the same simplification); later seasons use everyone.
        active = {pid: g for pid, g in on_board.items() if pid not in out_now}
        s1, picks, unfilled = lineup(active, slots, "exp_s1")
        s2 = lineup(on_board, slots, "exp_s2")[0]
        s3 = lineup(on_board, slots, "exp_s3")[0]
        stale = sum(1 for r in best.values() if r.get("manager") and r["manager"] != owner)
        summaries.append({
            "owner": owner, "players": len(ids), "on_board": len(on_board), "value": sum(values),
            "value_pos": positive, "s1": s1, "s2": s2, "s3": s3, "age": age,
            "rookie_share": rookie_share, "unfilled": unfilled, "picks": picks, "stale": stale,
            "top": sorted(best.values(), key=lambda r: r["value"] or float("-inf"), reverse=True)[:5],
        })
    return sorted(summaries, key=lambda t: t["value_pos"], reverse=True), absent


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", help="path to draft_board.csv (its sidecar must sit beside it)")
    args = parser.parse_args(argv)

    rows, meta = load_board(find_board(args.board))
    league, rosters, users, names = load_league()
    print(f"Board {meta.get('model_version')} generated {str(meta.get('generated_at'))[:16]}, "
          f"origin {meta.get('origin_target')}, rev {str(meta.get('git_revision'))[:8]}; "
          f"rosters from Sleeper capture, {league.get('total_rosters')} teams")
    for note in meta.get("known_limitations", []):
        print(f"  limitation: {note}")

    teams, absent = team_rows(by_sleeper_id(rows), league, rosters, users)
    print("\n=== Teams (value in season points over replacement; lineups in expected season points) ===")
    origin = int(meta.get("origin_target") or league.get("season"))
    seasons = "".join(f"{'lineup' + str(origin + k):>12}" for k in range(3))
    print(f"{'owner':<18}{'n':>4}{'board':>6}{'value+':>9}{'value':>9}{seasons}{'age':>6}{'rookie%':>8}")
    for t in teams:
        print(f"{t['owner']:<18}{t['players']:>4}{t['on_board']:>6}{t['value_pos']:>9.0f}{t['value']:>9.0f}"
              f"{t['s1']:>12.0f}{t['s2']:>12.0f}{t['s3']:>12.0f}{t['age']:>6.1f}{100 * t['rookie_share']:>7.0f}%")
    print("value+ sums each player's positive value; value includes negatives. lineup = best starting "
          "lineup on expected season points (no bench cover credited); this season excludes IR and taxi. "
          "age and rookie% are weighted by positive value.")

    print("\n=== Top five by value, each team ===")
    for t in teams:
        labels = ", ".join(f"{r['full_name']} ({r['fantasy_group']}) {r['value']:.0f}" for r in t["top"])
        print(f"  {t['owner']:<18} {labels}")

    unfilled = {t["owner"]: t["unfilled"] for t in teams if t["unfilled"]}
    if unfilled:
        print(f"\nStarting slots with no eligible board player this season: {unfilled}")
    stale = sum(t["stale"] for t in teams)
    if stale:
        print(f"\nNote: {stale} rostered players carry a different manager on the board than in this "
              "capture (the board was built from an earlier Sleeper capture).")
    if absent:
        print(f"\n{len(absent)} rostered players have no board row (no NFL identity yet, or a group the "
              "board does not value):")
        for owner, pid in absent:
            print(f"  {owner:<18} {names.get(pid) or pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
