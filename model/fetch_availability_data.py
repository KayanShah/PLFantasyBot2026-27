"""
Fetches real, point-in-time player availability (injury status, chance of
playing) for each past gameweek, from the Randdalf/fplcache archive — a
community project that has snapshotted FPL's bootstrap-static endpoint
4x/day since April 2021: https://github.com/Randdalf/fplcache

This is what the historical results dataset (vaastav/Fantasy-Premier-League)
is missing: it records what *happened* in each match, not what was known
about a player's fitness *before* that gameweek's deadline. Without this,
"avoid a player who's injured" can only be approximated after the fact
(e.g. via recent minutes) — this script gets the real thing.

Fetches snapshot files individually and on demand — no repo clone, no bulk
download. A single Git Trees API call lists every available snapshot's
path (~7600 entries, well under GitHub's per-call cap), which is then used
to pick, for each gameweek, the nearest snapshot taken safely before that
gameweek's deadline. Only that one small compressed file is downloaded per
gameweek, decompressed and parsed in memory, and reduced immediately to a
handful of fields per player — nothing else is kept, so local storage use
is a small per-season CSV, not the archive itself.

Re-running this overwrites data/historical/*/availability.csv, adding three
columns the first version discarded: `news`, `news_added` and `ep_next`. See
fetch_snapshot_availability() for why each matters — briefly, `status` and
`chance_of_playing` collapse "out for one week", "banned for three" and "left
the league in January" into the same number, and every attempt in plan.md so
far has had to treat them identically as a result.
"""

import lzma
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

REPO = "Randdalf/fplcache"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"
TREE_API = f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1"

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"

# The archive starts April 2021 — 2020-21 predates it entirely, skipped.
SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]

# How far before the first kickoff of a gameweek to look for a snapshot,
# so we're comfortably before the deadline (~90 min before kickoff) too.
DEADLINE_BUFFER = timedelta(hours=3)


def list_snapshot_datetimes() -> list[datetime]:
    """One API call, not per-day listing calls — avoids GitHub's 60/hr unauthenticated rate limit."""
    resp = requests.get(TREE_API, timeout=60)
    resp.raise_for_status()
    tree = resp.json()
    if tree.get("truncated"):
        raise RuntimeError("Git tree response was truncated — repo has grown too large for a single call")

    snapshots = []
    for entry in tree["tree"]:
        path = entry["path"]
        if not (path.startswith("cache/") and path.endswith(".json.xz")):
            continue
        # cache/{year}/{month}/{day}/{HHMM}.json.xz
        parts = path[len("cache/"):-len(".json.xz")].split("/")
        year, month, day, hhmm = parts
        dt = datetime(int(year), int(month), int(day), int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)
        snapshots.append((dt, path))
    snapshots.sort()
    return snapshots


def nearest_snapshot_before(snapshots: list[tuple[datetime, str]], target: datetime) -> str | None:
    best = None
    for dt, path in snapshots:
        if dt <= target:
            best = path
        else:
            break
    return best


def fetch_snapshot_availability(path: str) -> dict[str, dict]:
    """Downloads one snapshot, extracts {player_name: {status, chance_of_playing_*, news}}, discards the rest."""
    resp = requests.get(f"{RAW_BASE}/{path}", timeout=60)
    resp.raise_for_status()
    data = pd.io.common.BytesIO(resp.content)
    import json
    with lzma.open(data) as f:
        payload = json.load(f)

    result = {}
    for p in payload["elements"]:
        name = f"{p['first_name']} {p['second_name']}"
        result[name] = {
            "status": p["status"],
            "chance_of_playing_next_round": p["chance_of_playing_next_round"],
            "chance_of_playing_this_round": p["chance_of_playing_this_round"],
            # The status fields alone collapse three very different situations
            # into one number. `news` separates them, and it is already in every
            # snapshot -- this fetcher was simply throwing it away:
            #
            #   "Groin injury - Expected back 22 Aug"  -> a dated absence, so
            #     which future gameweeks are missed is knowable rather than
            #     guessed. build_horizon_scores() currently freezes availability
            #     flat across all five lookahead weeks; plan.md attempt 10 tried
            #     the opposite (assume recovery immediately) and it was worse.
            #     Neither is what the text actually says.
            #   "Suspended until 29 Aug"               -> a ban spanning several
            #     gameweeks, which plan.md attempt 7 flagged as uncovered: it
            #     only ever checked the immediately preceding gameweek.
            #   "has joined Porto on loan for the rest of the season."
            #     -> not a probability at all. The player never returns, so a
            #     squad slot is dead for months, and auto-subs cannot paper over
            #     that the way they cover a one-week injury.
            #
            # `news_added` timestamps the report, which is what plan.md's
            # "weight by time until the deadline" idea needs: a knock reported
            # three days out is worth more than one reported three weeks out.
            "news": p.get("news") or "",
            "news_added": p.get("news_added") or "",
            # FPL's own expected-points number for the coming gameweek. Never
            # used here, and worth having as a free external baseline to score
            # our predictions against.
            "ep_next": p.get("ep_next") or "",
        }
    return result


def season_gameweek_deadlines(season: str) -> dict[int, datetime]:
    fixtures = pd.read_csv(DATA_DIR / season / "fixtures.csv", encoding="utf-8", encoding_errors="ignore")
    fixtures["kickoff_time"] = pd.to_datetime(fixtures["kickoff_time"], utc=True)
    earliest_per_gw = fixtures.groupby("event")["kickoff_time"].min()
    return {int(gw): kt.to_pydatetime() - DEADLINE_BUFFER for gw, kt in earliest_per_gw.items() if pd.notna(gw)}


def fetch_season(season: str, snapshots: list[tuple[datetime, str]]) -> pd.DataFrame:
    deadlines = season_gameweek_deadlines(season)
    rows = []
    for gw in sorted(deadlines):
        target = deadlines[gw]
        path = nearest_snapshot_before(snapshots, target)
        if path is None:
            print(f"  GW{gw}: no snapshot available before {target.isoformat()} — skipping")
            continue
        availability = fetch_snapshot_availability(path)
        for name, info in availability.items():
            rows.append({"GW": gw, "name": name, **info})
        print(f"  GW{gw}: used {path} ({len(availability)} players)")
    return pd.DataFrame(rows)


def main() -> None:
    print("Listing available snapshots (single API call)...")
    snapshots = list_snapshot_datetimes()
    print(f"Found {len(snapshots)} snapshots spanning "
          f"{snapshots[0][0].date()} -> {snapshots[-1][0].date()}\n")

    for season in SEASONS:
        print(f"=== {season} ===")
        df = fetch_season(season, snapshots)
        out_path = DATA_DIR / season / "availability.csv"
        df.to_csv(out_path, index=False)
        print(f"  Saved {len(df)} rows -> {out_path}\n")


if __name__ == "__main__":
    main()
