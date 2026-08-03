# sleeping-giant

A Sleeper adapter for [DAVE](https://github.com/Winston722/dave-ledger), the
dynasty asset valuation engine. It pulls one league's live state and produces the
compact input DAVE consumes, so the engine can value a roster against the real
league instead of assumptions.

## Why it runs in CI

The environment where advice is generated cannot reach the Sleeper API. So the
sync runs on a GitHub Actions runner — which has open network — and commits the
data back to the repo. `.github/workflows/sync.yml` runs it on a fantasy-week
schedule (Wednesday after waivers, Friday, Sunday pre-kickoff) and on demand via
**Run workflow**.

## What it produces

```
data/raw/            verbatim Sleeper responses, committed so history is diffable
  league.json          league settings, including scoring_settings
  rosters.json         every team's roster
  users.json           owner display names
  state.json           current NFL week/season
  traded_picks.json    dynasty pick ownership
data/league_state.json DAVE's input contract: a flat list of rostered player IDs
```

`data/league_state.json` is the whole integration surface with DAVE:

```json
{
  "as_of": "2026-08-03",
  "id_type": "sleeper",
  "num_teams": 12,
  "rostered_player_ids": ["4034", "6794", "..."]
}
```

DAVE translates the Sleeper IDs itself and derives the free-agent pool by
subtraction, turning replacement level from an assumption into the real question:
*if I drop someone, who can I actually add?*

## Running it

```bash
python sync.py          # standard library only, no dependencies
```

Configuration is `config.json` (the league ID). Tests cover the pure assembly:

```bash
uv run --with pytest python -m pytest tests/ -q
```
