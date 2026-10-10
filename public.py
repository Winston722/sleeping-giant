#!/usr/bin/env python3
"""
Public dynasty leagues on Sleeper, for DAVE's real-league check of its league lab.

Owner approval, 2026-10-09: "I approve pulling public sleeper league data." DAVE's league lab finds the
conventional contend-or-rebuild cycle is not a best response, but the lab's careers are DAVE's own model and its
rivals are a caricature. The check is against real dynasty leagues: do teams that sell off for the future win more
titles later than comparable teams that do not? This module gathers what that needs. It is part of this
repository's canonical Sleeper acquisition and runs as `python sync.py public`.

**Discovery.** Sleeper has no league search. Starting from this repository's league, the crawl walks breadth first:
a league's managers (from its rosters) -> each manager's leagues in --season -> the dynasty ones (settings.type 2)
-> their managers, until --frontier leagues are queued. Each dynasty league is followed back through its seasons by `previous_league_id`; a chain is kept
when it has at least --min-seasons complete seasons.

**Kept per complete league season** (one file, data/public/leagues/<league_id>.json): the settings, roster
positions and scoring (`clean_league`); each roster's season totals (wins, points for, maximum possible points),
players, taxi and reserve (`clean_rosters`); the winners bracket; and every week's transactions (trades with their
draft picks, waivers, free-agent moves: `clean_transactions`).

**Never kept:** display names, usernames, avatars, team names, league names, chat messages, commissioner notes. Every
Sleeper user id is replaced by a keyed hash (`pseudonym`: HMAC-SHA256 under a local salt, data/public/.salt, never
committed), so a manager is the same pseudonym across leagues and seasons but cannot be looked up from the files.
Raw user ids exist only in memory while the crawl runs.

**Leagues like this one** (owner, 2026-10-10: "If we can find enough leagues that are at minimum 8 person
IDP/superflex dynasty, maybe we don't need to look at offense only leagues at all"). `--target idp-sf` keeps only
dynasty leagues of --min-teams to --max-teams teams that start a defensive player and play superflex
(`idp_superflex`), judged from the league object before any of its seasons is fetched, and discovers further leagues
only through the managers of those (IDP players tend to play in other IDP leagues).

**Player stats** (`--stats 2017-2025`): each season's totals for every player, in Sleeper's own stat keys
(data/public/stats/<season>.json), so DAVE can score every player under each league's own scoring and find each
league's replacement level, its bar. Public stats; nothing about any person beyond the player.

**Where.** data/public/ is gitignored: other people's leagues stay on the machine that pulled them, and dave-ledger
reads a derived panel. Resumable: a league season already on disk is not fetched again, and the crawl's queue is
kept in data/public/crawl.json (league ids only).

**Pace.** Sleeper asks callers to stay under 1,000 calls a minute. Calls go through one shared limiter (`Paced`): at
most --rate calls a second (8 by default: 480 a minute) however many are in flight; a season's weekly transactions are
fetched four at a time under it. Files are written whole (a temporary file renamed), so an interrupted crawl leaves
no partial season.

    python sync.py public [--max-chains 300] [--min-seasons 4] [--season 2025] [--rate 8]
        [--target idp-sf --min-teams 8 --max-teams 14] [--stats 2017-2025]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import secrets
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "data" / "public"
DYNASTY = 2
IDP_SLOTS = {"DL", "LB", "DB", "IDP_FLEX", "DE", "DT", "CB", "S"}      # Sleeper's defensive roster tokens

LEAGUE_KEYS = ("league_id", "season", "season_type", "status", "sport", "total_rosters", "previous_league_id",
               "roster_positions", "scoring_settings")
# settings worth keeping: the league's rules, nothing about its people
SETTINGS_KEYS = ("type", "num_teams", "playoff_teams", "playoff_week_start", "playoff_type", "playoff_round_type",
                 "playoff_seed_type", "league_average_match", "draft_rounds", "taxi_slots", "taxi_years",
                 "taxi_allow_vets", "taxi_deadline", "reserve_slots", "trade_deadline", "pick_trading",
                 "disable_trades", "waiver_type", "waiver_budget", "best_ball", "max_keepers", "leg")
ROSTER_SETTINGS_KEYS = ("wins", "losses", "ties", "fpts", "fpts_decimal", "fpts_against", "fpts_against_decimal",
                        "ppts", "ppts_decimal", "division", "waiver_budget_used")
TX_KEYS = ("transaction_id", "type", "status", "leg", "created", "status_updated", "roster_ids", "adds", "drops",
           "draft_picks", "waiver_budget", "consenter_ids")


def salt(path: Path = PUBLIC / ".salt") -> bytes:
    """The local key for pseudonyms: created once, never committed (data/public is gitignored)."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
    return bytes.fromhex(path.read_text().strip())


def pseudonym(user_id: Any, key: bytes) -> Optional[str]:
    """A Sleeper user id as a stable keyed hash (16 hex characters); None stays None."""
    if user_id in (None, "", "0", 0):
        return None
    return hmac.new(key, str(user_id).encode(), hashlib.sha256).hexdigest()[:16]


def clean_league(league: Dict[str, Any]) -> Dict[str, Any]:
    """A league object without its name, avatar, metadata or chat: the rules only."""
    out = {k: league.get(k) for k in LEAGUE_KEYS}
    out["settings"] = {k: (league.get("settings") or {}).get(k) for k in SETTINGS_KEYS}
    return out


def clean_rosters(rosters: List[Dict[str, Any]], key: bytes) -> List[Dict[str, Any]]:
    """Each roster's season totals and players, its managers as pseudonyms, without team names or metadata."""
    out = []
    for r in rosters or []:
        out.append({"roster_id": r.get("roster_id"),
                    "owner": pseudonym(r.get("owner_id"), key),
                    "co_owners": [pseudonym(u, key) for u in (r.get("co_owners") or [])],
                    "players": r.get("players") or [], "starters": r.get("starters") or [],
                    "taxi": r.get("taxi") or [], "reserve": r.get("reserve") or [],
                    "settings": {k: (r.get("settings") or {}).get(k) for k in ROSTER_SETTINGS_KEYS}})
    return out


def clean_transactions(txs: List[Dict[str, Any]], key: bytes) -> List[Dict[str, Any]]:
    """Trades, waivers and free-agent moves (rosters by roster id, the creator as a pseudonym), without notes."""
    out = []
    for t in txs or []:
        row = {k: t.get(k) for k in TX_KEYS}
        row["creator"] = pseudonym(t.get("creator"), key)
        out.append(row)
    return out


def idp_superflex(league: Dict[str, Any], min_teams: int = 8, max_teams: int = 14) -> bool:
    """A dynasty league of min_teams..max_teams teams that starts at least one defensive player and plays superflex
    (a SUPER_FLEX slot, or two QB slots)."""
    st = league.get("settings") or {}
    rp = league.get("roster_positions") or []
    n = int(st.get("num_teams") or league.get("total_rosters") or 0)
    return (st.get("type") == DYNASTY and min_teams <= n <= max_teams and any(p in IDP_SLOTS for p in rp)
            and ("SUPER_FLEX" in rp or rp.count("QB") >= 2))


def season_stats(get: Callable[[str], Any], out: Path, seasons: List[int]) -> List[int]:
    """Each season's player totals (`stats/nfl/regular/<season>`: player id -> Sleeper stat keys), written whole to
    out/stats/<season>.json unless already there. Returns the seasons written."""
    (out / "stats").mkdir(parents=True, exist_ok=True)
    done = []
    for season in seasons:
        path = out / "stats" / f"{season}.json"
        if path.exists():
            continue
        d = get(f"stats/nfl/regular/{season}") or {}
        keep = {str(pid): {k: v for k, v in (st or {}).items() if isinstance(v, (int, float))}
                for pid, st in d.items() if isinstance(st, dict)}
        write_whole(path, json.dumps(keep))
        done.append(season)
    return done


def owners(rosters: List[Dict[str, Any]]) -> List[str]:
    """The raw user ids managing a league's rosters (for discovery only; never written)."""
    ids = []
    for r in rosters or []:
        for u in [r.get("owner_id")] + list(r.get("co_owners") or []):
            if u not in (None, "", "0", 0):
                ids.append(str(u))
    return ids


class Crawler:
    """The breadth-first crawl (module docstring). `get` fetches a Sleeper path as JSON (injected for tests)."""

    def __init__(self, get: Callable[[str], Any], out: Path = PUBLIC, season: str = "2025", min_seasons: int = 4,
                 max_chains: int = 300, max_weeks: int = 18, key: Optional[bytes] = None, log: Callable = print,
                 workers: int = 1, frontier: int = 3000, target: Optional[Callable[[Dict[str, Any]], bool]] = None):
        self.get, self.out, self.season = get, out, str(season)
        self.min_seasons, self.max_chains, self.max_weeks = min_seasons, max_chains, max_weeks
        self.key = key if key is not None else salt(out / ".salt")
        self.log, self.workers, self.frontier, self.target = log, workers, frontier, target
        (out / "leagues").mkdir(parents=True, exist_ok=True)
        st = out / "crawl.json"
        state = json.loads(st.read_text()) if st.exists() else {}
        self.queue = deque(state.get("queue", []))
        self.seen_leagues = set(state.get("seen_leagues", []))
        self.seen_users = set(state.get("seen_users", []))          # pseudonyms
        self.chains = state.get("chains", {})                         # head league id -> its kept season ids
        self.retries: Dict[str, int] = {}

    def save_state(self):
        write_whole(self.out / "crawl.json", json.dumps({
            "queue": list(self.queue), "seen_leagues": sorted(self.seen_leagues),
            "seen_users": sorted(self.seen_users), "chains": self.chains}, indent=0))

    def chain(self, league: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The league and its earlier seasons, newest first (each a league object)."""
        out, cur, guard = [league], league, 0
        while cur.get("previous_league_id") not in (None, "", "0") and guard < 30:
            cur = self.get(f"league/{cur['previous_league_id']}")
            if not cur:
                break
            out.append(cur)
            guard += 1
        return out

    def keep_season(self, league: Dict[str, Any]) -> None:
        """Fetch and write one complete league season (unless already on disk)."""
        lid = str(league["league_id"])
        path = self.out / "leagues" / f"{lid}.json"
        if path.exists():
            return
        paths = [f"league/{lid}/rosters", f"league/{lid}/winners_bracket"] + \
            [f"league/{lid}/transactions/{week}" for week in range(1, self.max_weeks + 1)]
        if self.workers > 1:
            with ThreadPoolExecutor(self.workers) as ex:
                got = list(ex.map(self.get, paths))
        else:
            got = [self.get(p_) for p_ in paths]
        rosters, bracket = got[0] or [], got[1] or []
        txs = [t for week in got[2:] for t in clean_transactions(week or [], self.key)]
        write_whole(path, json.dumps({"league": clean_league(league), "rosters": clean_rosters(rosters, self.key),
                                      "winners_bracket": bracket, "transactions": txs}))

    def discover(self, rosters: List[Dict[str, Any]]) -> None:
        """Queue every dynasty league in --season of each new manager of `rosters`."""
        for uid in owners(rosters):
            p = pseudonym(uid, self.key)
            if p in self.seen_users:
                continue
            self.seen_users.add(p)
            for lg in self.get(f"user/{uid}/leagues/nfl/{self.season}") or []:
                lid = str(lg.get("league_id"))
                if ((lg.get("settings") or {}).get("type") == DYNASTY and (self.target is None or self.target(lg))
                        and lid not in self.seen_leagues):
                    self.seen_leagues.add(lid)
                    self.queue.append(lid)

    def _one(self, lid: str) -> None:
        """One queued league: keep its chain if long enough (every season written, or none), then discover."""
        league = self.get(f"league/{lid}")
        if not league or (league.get("settings") or {}).get("type") != DYNASTY:
            return
        if self.target is not None and not self.target(league):     # not a league like this one: no chain, no spread
            return
        seasons = [s for s in self.chain(league) if s.get("status") == "complete"]
        if len(seasons) >= self.min_seasons:
            for s in seasons:
                self.keep_season(s)
            self.chains[lid] = [str(s["league_id"]) for s in seasons]
            self.log(f"  chain {len(self.chains)}/{self.max_chains}: {len(seasons)} seasons "
                     f"({min(s['season'] for s in seasons)}-{max(s['season'] for s in seasons)}); "
                     f"queue {len(self.queue)}")
        if len(self.queue) < self.frontier:                    # enough leagues queued: stop looking for more
            self.discover(self.get(f"league/{lid}/rosters") or [])

    def run(self, start: str) -> Dict[str, Any]:
        """Crawl from league `start` until --max-chains chains are kept or the frontier is empty."""
        if not self.queue and start not in self.seen_leagues:
            self.seen_leagues.add(start)
            self.discover(self.get(f"league/{start}/rosters") or [])
        while self.queue and len(self.chains) < self.max_chains:
            lid = self.queue.popleft()
            try:
                self._one(lid)
            except FetchError as e:                            # the network failed it: try again later, nothing kept
                self.retries[lid] = self.retries.get(lid, 0) + 1
                if self.retries[lid] < 3:
                    self.queue.append(lid)
                self.log(f"  league put back ({self.retries[lid]}): {e}")
            self.save_state()
        self.save_state()
        return {"chains": len(self.chains), "seasons": sum(len(v) for v in self.chains.values()),
                "queue": len(self.queue), "managers_seen": len(self.seen_users)}


def write_whole(path: Path, text: str) -> None:
    """Write a file whole: a temporary file renamed over it, so an interruption leaves the old file or none."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


class FetchError(Exception):
    """A call that kept failing at the network level: the league is put back on the queue, nothing written."""


class Paced:
    """`get` behind one limiter shared by every thread: calls start at least 1 / rate seconds apart (Sleeper: stay
    under 1,000 calls a minute). A league gone private or deleted (`get` gives up: RuntimeError) reads as None. A
    dropped connection is retried with backoff (`backoff` seconds, doubling) and, after `tries`, raises FetchError."""

    def __init__(self, get: Callable[[str], Any], rate: float, tries: int = 4, backoff: float = 2.0):
        self.get, self.gap, self.tries, self.backoff = get, 1.0 / rate, tries, backoff
        self.lock, self.next = threading.Lock(), time.monotonic()

    def _slot(self):
        with self.lock:
            now = time.monotonic()
            wait = self.next - now
            self.next = max(now, self.next) + self.gap
        if wait > 0:
            time.sleep(wait)

    def __call__(self, path: str) -> Any:
        delay = self.backoff
        for attempt in range(self.tries):
            self._slot()
            try:
                return self.get(path)
            except RuntimeError:
                return None
            except (OSError, http.client.HTTPException) as e:      # reset, refused, closed without a response
                if attempt == self.tries - 1:
                    raise FetchError(f"{path}: {e}") from e
                time.sleep(delay)
                delay *= 2


def main(argv: Optional[List[str]] = None) -> int:
    import sync
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-chains", type=int, default=300)
    ap.add_argument("--min-seasons", type=int, default=4)
    ap.add_argument("--season", default="2025", help="the season whose dynasty leagues seed each chain")
    ap.add_argument("--rate", type=float, default=8.0, help="calls a second at most (Sleeper: under ~16)")
    ap.add_argument("--workers", type=int, default=4, help="a season's calls in flight at once, under --rate")
    ap.add_argument("--frontier", type=int, default=3000, help="stop discovering once this many leagues are queued")
    ap.add_argument("--target", choices=("idp-sf",), default=None,
                    help="idp-sf: only dynasty leagues that start a defender and play superflex (`idp_superflex`)")
    ap.add_argument("--min-teams", type=int, default=8)
    ap.add_argument("--max-teams", type=int, default=14)
    ap.add_argument("--stats", default=None, help="player season totals for these seasons first, e.g. 2017-2025")
    a = ap.parse_args(argv)
    cfg = json.loads((ROOT / "config.json").read_text())
    get = Paced(sync._get, a.rate)
    if a.stats:
        lo, _, hi = a.stats.partition("-")
        print(f"player stats written for {season_stats(get, PUBLIC, list(range(int(lo), int(hi or lo) + 1)))}",
              flush=True)
    target = (lambda lg: idp_superflex(lg, a.min_teams, a.max_teams)) if a.target == "idp-sf" else None
    cr = Crawler(get, season=a.season, min_seasons=a.min_seasons, max_chains=a.max_chains,
                 workers=a.workers, frontier=a.frontier, target=target)
    print(f"crawling public dynasty leagues from {cfg['league_id']} (season {a.season}, chains of "
          f"{a.min_seasons}+ complete seasons, up to {a.max_chains})", flush=True)
    print(json.dumps(cr.run(str(cfg["league_id"]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
