"""
Scrapes all Premier League fixtures for the current season from the
official Fantasy Premier League API and writes them to CSV and JSON.

Data source: https://fantasy.premierleague.com/api/fixtures/
Team names resolved via: https://fantasy.premierleague.com/api/bootstrap-static/
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"


def fetch_json(url: str) -> dict | list:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def build_team_lookup(bootstrap: dict) -> dict[int, dict]:
    return {
        team["id"]: {"name": team["name"], "short_name": team["short_name"]}
        for team in bootstrap["teams"]
    }


def format_kickoff(kickoff_iso: str | None) -> str:
    if not kickoff_iso:
        return "TBC"
    dt = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def build_fixture_rows(fixtures: list[dict], teams: dict[int, dict]) -> list[dict]:
    rows = []
    for fixture in fixtures:
        home = teams[fixture["team_h"]]
        away = teams[fixture["team_a"]]
        rows.append(
            {
                "gameweek": fixture["event"],
                "kickoff": format_kickoff(fixture["kickoff_time"]),
                "home_team": home["name"],
                "away_team": away["name"],
                "home_short": home["short_name"],
                "away_short": away["short_name"],
                "home_difficulty": fixture["team_h_difficulty"],
                "away_difficulty": fixture["team_a_difficulty"],
                "finished": fixture["finished"],
                "home_score": fixture["team_h_score"],
                "away_score": fixture["team_a_score"],
                "fixture_id": fixture["id"],
            }
        )
    rows.sort(key=lambda r: (r["gameweek"] is None, r["gameweek"] or 0, r["kickoff"]))
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    bootstrap = fetch_json(BOOTSTRAP_URL)
    fixtures = fetch_json(FIXTURES_URL)

    teams = build_team_lookup(bootstrap)
    rows = build_fixture_rows(fixtures, teams)

    csv_path = OUTPUT_DIR / "fixtures.csv"
    json_path = OUTPUT_DIR / "fixtures.json"
    write_csv(rows, csv_path)
    write_json(rows, json_path)

    print(f"Scraped {len(rows)} fixtures.")
    print(f"CSV  -> {csv_path}")
    print(f"JSON -> {json_path}")


if __name__ == "__main__":
    main()
