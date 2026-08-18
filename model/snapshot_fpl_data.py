"""
Archives the live FPL API into this repo, the way Randdalf/fplcache does for
the whole community (see fetch_availability_data.py's docstring) -- except
this is our own copy, for our own season, so it never depends on someone
else's archive staying online or getting to a snapshot before our own
30-minute cron does.

Two things get written on every run:

  data/snapshots/latest.json.xz   Always overwritten -- a full, fresh,
      restorable backup of bootstrap-static + fixtures. One file, no growth.

  data/snapshots/daily/{date}.json.xz   Written once per UTC day (first run
      of the day creates it, later runs that day are no-ops for this file) --
      a slow-growing dated archive in fplcache's own format (~85KB/day
      compressed), useful for anything that later wants "the state of the
      world on date X" the way fetch_availability_data.py already mines
      fplcache for that.

  data/snapshots/changelog.jsonl   Appended to only when something tracked
      actually changed since the previous run: a player's price, status,
      chance of playing, or news -- or a fixture's difficulty rating for
      either side. A 30-minute full-snapshot cadence would otherwise write
      ~13,000 near-duplicate rows over a season for no benefit -- most
      players and fixtures don't change anything most half-hours. This is
      the part that's actually more useful than fplcache's fixed 4x/day
      cadence: a price rise, a fresh injury note, or a re-rated fixture
      lands here within 30 minutes of FPL publishing it, not up to 6 hours
      later.
"""

import json
import lzma
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://fantasy.premierleague.com/api"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"
LATEST_PATH = OUT_DIR / "latest.json.xz"
DAILY_DIR = OUT_DIR / "daily"
CHANGELOG_PATH = OUT_DIR / "changelog.jsonl"
STATE_PATH = OUT_DIR / "last_state.json"

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

    LATEST_PATH.write_bytes(compressed)
    print(f"Wrote {LATEST_PATH} ({len(compressed) / 1024:.0f} KB)")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
