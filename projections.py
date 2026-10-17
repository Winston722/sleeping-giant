"""Sleeper's weekly player projections, for DAVE's lineup study (owner, 2026-10-17: "Most folks just use Sleeper's
projections to set their lineup ... Can you run a test comparing how different things would be by using sleepers
projections to set lineups instead?").

    python sync.py projections --seasons 2020-2026 [--weeks 1-18] [--out data/projections]

Each regular-season week's projections (`/v1/projections/nfl/regular/{season}/{week}`), as Sleeper published them
for that week. Kept per player: Sleeper's player id and each projected stat (Sleeper's stat keys, the same as the
league's scoring keys), for players with any projected stat. Nothing else (no names, no ranks).

Each week's fetch time is recorded in `<season>/fetched_at.json` (week -> UTC time), so DAVE can check that the
designations it reads beside them were captured at about the same time (Sol, 2026-10-17).

**Retention:** the files stay in `data/projections/` (gitignored), on the machine that pulled them; dave-ledger
commits only derived aggregates. Sleeper's terms: owner reviewed, current use fine (dave-ledger
docs/sources-and-obligations.md C11). Paced well under Sleeper's stated limit (1,000 calls a minute).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

from sync import ROOT, _get

PACE = 0.25                 # seconds between calls
SKIP = ("adp", "pos_adp", "rank", "pos_rank")   # rank and draft fields: not projections


def _span(text: str) -> list[int]:
    lo, _, hi = text.partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def week(season: int, wk: int) -> dict:
    """player_id -> {stat: value}, one week's projections."""
    raw = _get(f"projections/nfl/regular/{season}/{wk}")
    rows = raw.items() if isinstance(raw, dict) else ((r.get("player_id"), r.get("stats") or {}) for r in raw)
    out = {}
    for pid, stats in rows:
        if not pid or not isinstance(stats, dict):
            continue
        kept = {k: float(v) for k, v in stats.items()
                if isinstance(v, (int, float)) and v and not any(k.startswith(s) for s in SKIP)}
        if kept:
            out[str(pid)] = kept
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", required=True)
    ap.add_argument("--weeks", default="1-18")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "projections")
    a = ap.parse_args(argv)
    n = 0
    for season in _span(a.seasons):
        d = a.out / str(season)
        d.mkdir(parents=True, exist_ok=True)
        stamps_f = d / "fetched_at.json"
        stamps = json.loads(stamps_f.read_text()) if stamps_f.exists() else {}
        for wk in _span(a.weeks):
            proj = week(season, wk)
            n += 1
            if proj:
                (d / f"{wk}.json").write_text(json.dumps(proj, sort_keys=True, separators=(",", ":")))
                stamps[str(wk)] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                stamps_f.write_text(json.dumps(stamps, indent=1, sort_keys=True) + "\n")
            print(f"  {season} week {wk}: {len(proj)} players", flush=True)
            time.sleep(PACE)
    print(f"{n} calls -> {a.out}")
    return 0
