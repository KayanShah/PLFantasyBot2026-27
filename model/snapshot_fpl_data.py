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

  data/snapshots/changelog.jsonl   Appended to only when something about a
      player actually changed since the previous run: price, status, chance
      of playing, or news. A 30-minute full-snapshot cadence would otherwise
      write ~13,000 near-duplicate rows over a season for no benefit -- most
      players don't change anything most half-hours. This is the part that's
      actually more useful than fplcache's fixed 4x/day cadence: a price rise
      or a fresh injury note lands here within 30 minutes of FPL publishing
      it, not up to 6 hours later.
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

# The fields worth logging a change for. Everything else in bootstrap-static
# (form, ICT index, points, ...) changes constantly during live play and is
# already captured properly, with real match context, by
# model/live_pipeline.py's sync_season() -- duplicating that here would just
# make the changelog noisy. This tracks what a manager actually needs to see
# *between* gameweeks: price moves and availability news.
TRACKED_FIELDS = [
    "now_cost", "status", "chance_of_playing_next_round",
    "chance_of_playing_this_round", "news",
]


def fetch(path: str) -> dict:
    response = requests.get(f"{API}/{path}", timeout=60)
    response.raise_for_status()
    return response.json()


def compact_state(bootstrap: dict) -> dict[int, dict]:
    return {
        e["id"]: {field: e.get(field) for field in TRACKED_FIELDS}
        for e in bootstrap["elements"]
    }


def diff_state(previous: dict[int, dict], current: dict[int, dict], names: dict[int, str]) -> list[dict]:
    changes = []
    for element_id, fields in current.items():
        prior = previous.get(str(element_id)) or previous.get(element_id)
        if prior is None:
            continue  # new to the dataset (e.g. a transfer window signing) -- nothing to diff against yet
        for field, value in fields.items():
            if prior.get(field) != value:
                changes.append({
                    "element": element_id, "name": names.get(element_id, "?"),
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

    names = {e["id"]: e["web_name"] for e in bootstrap["elements"]}
    current = compact_state(bootstrap)
    previous = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}

    changes = diff_state(previous, current, names)
    if changes:
        now = datetime.now(timezone.utc).isoformat()
        with CHANGELOG_PATH.open("a", encoding="utf-8") as f:
            for change in changes:
                f.write(json.dumps({"at": now, **change}) + "\n")
        print(f"Logged {len(changes)} change(s) -> {CHANGELOG_PATH}")
    else:
        print("No tracked-field changes since last snapshot.")

    STATE_PATH.write_text(json.dumps(current), encoding="utf-8")


if __name__ == "__main__":
    main()
