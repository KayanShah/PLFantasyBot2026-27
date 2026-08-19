"""
Archives the live FPL API into this repo, the way Randdalf/fplcache does for
the whole community (see fetch_availability_data.py's docstring) -- except
this is our own copy, for our own season, so it never depends on someone
else's archive staying online or getting to a snapshot before our own
30-minute cron does.

Written on every run, at two resolutions:

  data/snapshots/history/{year}/{month}/{day}/{HHMM}.json.xz   Every run's
      full snapshot, but only for the last HISTORY_RETENTION_DAYS (7) --
      pruned each run, oldest first. This is the "keep everything" layer,
      deliberately time-boxed: full 30-minute resolution is only actually
      useful for recent history (what changed a few hours ago), and keeping
      it forever was ~1.6GB/year for a benefit that fades fast. Uses
      fplcache's own path convention (cache/{y}/{m}/{d}/{HHMM}.json.xz there
      vs history/... here) so fetch_availability_data.py's existing
      nearest-snapshot-before() logic works against this folder unchanged.

  data/snapshots/daily/{date}.json.xz   One snapshot per UTC day, kept
      forever -- never pruned. This is what survives once a day ages out of
      history/: full 30-minute resolution for the last week, daily
      resolution for everything before that. ~33MB/year, small enough to
      never worry about.

  data/snapshots/latest.json.xz   Always overwritten -- the same payload as
      the newest history/ file, duplicated here so "the current state" never
      needs a directory scan to find.

  data/snapshots/changelog.jsonl   Appended to only when something tracked
      actually changed since the previous run: a player's price, status,
      chance of playing, or news -- or a fixture's difficulty rating for
      either side. Never pruned (it only ever grows by real changes, not by
      time, so there's nothing to thin).
"""

import json
import lzma
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API = "https://fantasy.premierleague.com/api"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"
HISTORY_DIR = OUT_DIR / "history"
LATEST_PATH = OUT_DIR / "latest.json.xz"
DAILY_DIR = OUT_DIR / "daily"
CHANGELOG_PATH = OUT_DIR / "changelog.jsonl"
STATE_PATH = OUT_DIR / "last_state.json"

# How long full 30-minute-resolution snapshots are kept before being pruned
# down to just the once-daily anchor in daily/ (which is never pruned).
HISTORY_RETENTION_DAYS = 7

# The player fields worth logging a change for. Everything else in
# bootstrap-static (form, ICT index, points, ...) changes constantly during
# live play and is already captured properly, with real match context, by
# model/live_pipeline.py's sync_season() -- duplicating that here would just
# make the changelog noisy. This tracks what a manager actually needs to see
# *between* gameweeks: price moves and availability news.
TRACKED_FIELDS = [
    "now_cost", "status", "chance_of_playing_next_round",
    "chance_of_playing_this_round", "news",
]

# Fixture difficulty ("match hardness") -- FPL does occasionally re-rate a
# fixture mid-season (a team's form or a key absence changes how hard a game
# looks), and that's exactly the kind of thing a manager wants to know about
# within 30 minutes rather than stumbling on next time they check prices.
FIXTURE_FIELDS = ["team_h_difficulty", "team_a_difficulty"]


def fetch(path: str) -> dict:
    response = requests.get(f"{API}/{path}", timeout=60)
    response.raise_for_status()
    return response.json()


def compact_player_state(bootstrap: dict) -> dict[int, dict]:
    return {
        e["id"]: {field: e.get(field) for field in TRACKED_FIELDS}
        for e in bootstrap["elements"]
    }


def compact_fixture_state(fixtures: list[dict]) -> dict[int, dict]:
    return {f["id"]: {field: f.get(field) for field in FIXTURE_FIELDS} for f in fixtures}


def prune_old_history(now_utc: datetime) -> int:
    """
    Deletes history/ snapshots more than HISTORY_RETENTION_DAYS old. Safe to
    do unconditionally: daily/ already holds one full snapshot for every day
    that's ever been run, written independently the first time that day is
    seen -- by the time a day ages out of the 7-day window here, its
    daily/{date}.json.xz already exists, so nothing is lost, only thinned
    from 48 snapshots/day down to 1.

    The date is read from the file's own path (history/{y}/{m}/{d}/{HHMM}),
    not filesystem mtime -- mtime can be rewritten by a fresh checkout and
    wouldn't reflect when the snapshot actually happened.
    """
    if not HISTORY_DIR.exists():
        return 0

    cutoff = now_utc - timedelta(days=HISTORY_RETENTION_DAYS)
    removed = 0
    for path in HISTORY_DIR.glob("*/*/*/*.json.xz"):
        try:
            year, month, day = (int(part) for part in path.parts[-4:-1])
            # path.stem only strips one suffix -- "1200.json.xz" -> "1200.json",
            # not "1200" -- so read the HHMM digits straight off the filename.
            hhmm = path.name[:4]
            snapshot_at = datetime(year, month, day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue  # not a recognizable {HHMM}.json.xz snapshot -- leave it alone
        if snapshot_at < cutoff:
            path.unlink()
            removed += 1

    # Clean up day/month/year directories a prune just emptied, deepest first.
    for depth in (3, 2, 1):
        for directory in HISTORY_DIR.glob("/".join(["*"] * depth)):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    return removed


def diff_state(previous: dict, current: dict, id_key: str, labels: dict) -> list[dict]:
    """Generic field-level diff, shared by players and fixtures -- `labels`
    maps id -> a human-readable label for the changelog row."""
    changes = []
    for item_id, fields in current.items():
        prior = previous.get(str(item_id)) or previous.get(item_id)
        if prior is None:
            continue  # new to the dataset (e.g. a transfer window signing, or a rescheduled fixture) -- nothing to diff against yet
        for field, value in fields.items():
            if prior.get(field) != value:
                changes.append({
                    id_key: item_id, "name": labels.get(item_id, "?"),
                    "field": field, "from": prior.get(field), "to": value,
                })
    return changes


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching live FPL data...")
    bootstrap = fetch("bootstrap-static/")
    fixtures = fetch("fixtures/")
    payload = {"bootstrap": bootstrap, "fixtures": fixtures}
    raw = json.dumps(payload).encode("utf-8")
    compressed = lzma.compress(raw, preset=6)

    now_utc = datetime.now(timezone.utc)
    history_path = (
        HISTORY_DIR / f"{now_utc.year}" / f"{now_utc.month:02d}" / f"{now_utc.day:02d}"
        / f"{now_utc.strftime('%H%M')}.json.xz"
    )
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_bytes(compressed)
    print(f"Wrote {history_path} ({len(compressed) / 1024:.0f} KB)")

    pruned = prune_old_history(now_utc)
    if pruned:
        print(f"Pruned {pruned} history/ snapshot(s) older than {HISTORY_RETENTION_DAYS} days "
              f"(their day's daily/ anchor is kept)")

    LATEST_PATH.write_bytes(compressed)
    print(f"Wrote {LATEST_PATH} ({len(compressed) / 1024:.0f} KB)")

    today = now_utc.strftime("%Y-%m-%d")
    daily_path = DAILY_DIR / f"{today}.json.xz"
    if not daily_path.exists():
        daily_path.write_bytes(compressed)
        print(f"Wrote {daily_path} (first snapshot of the day)")

    player_names = {e["id"]: e["web_name"] for e in bootstrap["elements"]}
    team_names = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    fixture_labels = {
        f["id"]: f"{team_names.get(f['team_h'], '?')} v {team_names.get(f['team_a'], '?')} (GW{f.get('event')})"
        for f in fixtures
    }

    current_state = {
        "players": compact_player_state(bootstrap),
        "fixtures": compact_fixture_state(fixtures),
    }
    previous_state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    # Back-compat with the pre-fixture-tracking state file, which was just
    # the player dict at the top level rather than nested under "players".
    if previous_state and "players" not in previous_state:
        previous_state = {"players": previous_state, "fixtures": {}}

    changes = (
        diff_state(previous_state.get("players", {}), current_state["players"], "element", player_names)
        + diff_state(previous_state.get("fixtures", {}), current_state["fixtures"], "fixture", fixture_labels)
    )
    if changes:
        now = datetime.now(timezone.utc).isoformat()
        with CHANGELOG_PATH.open("a", encoding="utf-8") as f:
            for change in changes:
                f.write(json.dumps({"at": now, **change}) + "\n")
        print(f"Logged {len(changes)} change(s) -> {CHANGELOG_PATH}")
    else:
        print("No tracked-field changes since last snapshot.")

    STATE_PATH.write_text(json.dumps(current_state), encoding="utf-8")


if __name__ == "__main__":
    main()
