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
  injuries/<date>.json every NFL player Sleeper lists with an injury status (body part,
                       notes such as "Surgery" or "Knee - ACL", practice participation),
                       one dated snapshot per sync: Sleeper keeps no history of these
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

## Public dynasty leagues (`python sync.py public`)

Owner approval, 2026-10-09: "I approve pulling public sleeper league data." DAVE's league lab finds that the
conventional contend-or-rebuild cycle is not a best response; this gathers real dynasty leagues to check that
against what real managers did and won. `public.py` walks breadth first from this league: its managers, their
other dynasty leagues in a season, those leagues' managers, and so on, following each league back through its
seasons (`previous_league_id`) and keeping chains with at least four complete seasons.

- **Kept** (data/public/leagues/<league_id>.json, one complete league season each): the league's rules (settings,
  roster positions, scoring), each roster's season totals (wins, points for, maximum possible points), players, taxi
  and reserve, the winners bracket, and the season's transactions (trades with their draft picks, waivers, free-agent
  moves).
- **Never kept:** display names, usernames, avatars, team names, league names, chat, notes. Each Sleeper user id is
  a keyed hash (HMAC-SHA256 under data/public/.salt, generated locally), so a manager is a stable pseudonym that
  cannot be looked up from the files. Raw user ids exist only in memory during the crawl.
- **Not committed:** data/public/ is gitignored. Other people's leagues stay on the machine that pulled them;
  dave-ledger reads a derived panel.
- **Pace:** one rate limiter shared by every request (8 a second by default, 480 a minute; Sleeper asks for under 1,000), a season's weekly transactions four at a time under it. Files are written whole (renamed into place).
  Resumable from data/public/crawl.json (league ids and pseudonyms only).

```bash
python sync.py public --max-chains 300 --min-seasons 4 --season 2025 --rate 8
```

- **Leagues like this one** (owner, 2026-10-10): `--target idp-sf --min-teams 8 --max-teams 16` keeps only dynasty
  leagues that start a defender and play superflex, judged before any season is fetched, and discovers further
  leagues only through those leagues' managers.
- **Player stats:** `--stats 2017-2025` first writes each season's player totals in Sleeper's stat keys
  (data/public/stats/<season>.json, public player data), so DAVE can score every player under each league's own
  rules and find each league's bar (dave-ledger `scripts/study/league_bars.py`).

```bash
python sync.py public --target idp-sf --min-teams 8 --max-teams 16 --min-seasons 2 --stats 2017-2025 --rate 10
```

## League analysis

`analyze.py` reads DAVE's published board (v5 since 2026-09-29) and adds the one
thing DAVE's output does not carry for the current moment: who owns whom, from
this repo's capture. It imports nothing from DAVE and needs only the standard
library; players join on the board's `sleeper_id`.

```bash
python analyze.py                          # board at ../dave-ledger/output/
python analyze.py --board path/to/draft_board.csv
```

Per team: summed value (season points over replacement; a two-group player
counts once, at the better row), the best starting lineup on expected season
points for the next three seasons (IR and taxi players sit out this season),
value-weighted age and rookie share, and the top five players. The board is
refused unless it matches its sidecar's `board_sha256`, and a pre-v5 board is
refused by its columns.

## The insights layer

**Not served by DAVE v5.** v5 publishes no `player_briefs.jsonl` or
`player_explanations.jsonl` (DAVE-ID's knowledge layer is not rebuilt yet), so
`explain.py` has nothing to render against a v5 board; the description below is
the v2-era design, kept for when that layer returns.

`explain.py` **serves the words DAVE already wrote.** DAVE-ID publishes
`player_briefs.jsonl` — a structured brief plus deterministic prose per board
row — beside the `player_explanations.jsonl` ledger it is derived from. This
module renders it.

It used to derive that prose itself, and that was the bug. The contract's
language rules (a rookie's ~0.99 hit probability is never a success chance,
schedule share is never called availability, talent numbers are never compared
across populations, long value tails are expectation and never confidence) are
semantic claims about DAVE's own state. Enforcing them here kept them a
repository away from the semantics they constrain, guarded by a docstring
rather than a suite — so a change in DAVE would leave them stale in silence.
They now live in `dave_ledger/analysis/prose.py`, tested beside the contract.

What stays here is the consumer's own job: refusing a mixed generation. All
four artifacts are renamed individually, so nothing is rendered until
`context.board_sha256 == meta.board_sha256 == sha256(draft_board.csv)` and both
`meta.explanations.sha256` and `meta.briefs.sha256` match their files.

```bash
python explain.py "Josh Allen" "Brock Bowers"   # prose per player
python explain.py --top 10                       # the board's top ten
python explain.py --top 5 --prompt               # LLM-ready briefs + rules
```

## Running the sync

```bash
python sync.py          # standard library only, no dependencies
```

Configuration is `config.json` (the league ID). Tests cover the pure assembly:

```bash
uv run --with pytest python -m pytest tests/ -q
```
