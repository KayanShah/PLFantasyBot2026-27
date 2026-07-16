"""
Builds a single SQLite database containing all scraped FPL data —
positions, teams, players, gameweeks, and fixtures — ready to open
directly in DB Browser for SQLite.

Data source: https://fantasy.premierleague.com/api/bootstrap-static/
             https://fantasy.premierleague.com/api/fixtures/
"""

import json
import sqlite3
from pathlib import Path

import requests

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fpl.db"

SCHEMA = """
CREATE TABLE positions (
    id              INTEGER PRIMARY KEY,
    singular_name   TEXT,
    short_name      TEXT,
    squad_min_play  INTEGER,
    squad_max_play  INTEGER,
    squad_select    INTEGER
);

CREATE TABLE teams (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT,
    short_name              TEXT,
    strength                INTEGER,
    strength_overall_home   INTEGER,
    strength_overall_away   INTEGER,
    strength_attack_home    INTEGER,
    strength_attack_away    INTEGER,
    strength_defence_home   INTEGER,
    strength_defence_away   INTEGER,
    played                  INTEGER,
    win                     INTEGER,
    draw                    INTEGER,
    loss                    INTEGER,
    points                  INTEGER,
    position                INTEGER
);

CREATE TABLE gameweeks (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT,
    deadline_time           TEXT,
    finished                INTEGER,
    is_current              INTEGER,
    is_next                 INTEGER,
    average_entry_score     INTEGER,
    highest_score           INTEGER,
    most_selected           INTEGER,
    most_transferred_in     INTEGER,
    most_captained          INTEGER,
    most_vice_captained     INTEGER,
    top_element              INTEGER
);

CREATE TABLE players (
    id                          INTEGER PRIMARY KEY,
    first_name                  TEXT,
    second_name                 TEXT,
    web_name                    TEXT,
    team_id                     INTEGER REFERENCES teams(id),
    position_id                 INTEGER REFERENCES positions(id),
    now_cost                    INTEGER,
    status                      TEXT,
    news                        TEXT,
    chance_of_playing_next_round INTEGER,
    total_points                INTEGER,
    event_points                INTEGER,
    points_per_game              REAL,
    form                        REAL,
    ep_this                     REAL,
    ep_next                     REAL,
    selected_by_percent         REAL,
    value_form                  REAL,
    value_season                REAL,
    minutes                     INTEGER,
    starts                      INTEGER,
    goals_scored                INTEGER,
    assists                     INTEGER,
    clean_sheets                INTEGER,
    goals_conceded              INTEGER,
    own_goals                   INTEGER,
    penalties_saved             INTEGER,
    penalties_missed            INTEGER,
    yellow_cards                INTEGER,
    red_cards                   INTEGER,
    saves                       INTEGER,
    bonus                       INTEGER,
    bps                         INTEGER,
    influence                   REAL,
    creativity                  REAL,
    threat                      REAL,
    ict_index                   REAL,
    clearances_blocks_interceptions INTEGER,
    recoveries                  INTEGER,
    tackles                     INTEGER,
    defensive_contribution       INTEGER,
    expected_goals               REAL,
    expected_assists             REAL,
    expected_goal_involvements   REAL,
    expected_goals_conceded       REAL,
    transfers_in                 INTEGER,
    transfers_out                INTEGER,
    transfers_in_event           INTEGER,
    transfers_out_event          INTEGER
);

CREATE TABLE fixtures (
    id                  INTEGER PRIMARY KEY,
    event                INTEGER REFERENCES gameweeks(id),
    kickoff_time         TEXT,
    team_h               INTEGER REFERENCES teams(id),
    team_a               INTEGER REFERENCES teams(id),
    team_h_score         INTEGER,
    team_a_score         INTEGER,
    team_h_difficulty    INTEGER,
    team_a_difficulty    INTEGER,
    started              INTEGER,
    finished              INTEGER
);
"""


def fetch_json(url: str) -> dict | list:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict], columns: list[str]) -> None:
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    sql = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(row.get(col) for col in columns) for row in rows])


def load_positions(conn: sqlite3.Connection, element_types: list[dict]) -> None:
    columns = ["id", "singular_name", "short_name", "squad_min_play", "squad_max_play", "squad_select"]
    rows = [
        {
            "id": et["id"],
            "singular_name": et["singular_name"],
            "short_name": et["singular_name_short"],
            "squad_min_play": et["squad_min_play"],
            "squad_max_play": et["squad_max_play"],
            "squad_select": et["squad_select"],
        }
        for et in element_types
    ]
    insert_rows(conn, "positions", rows, columns)


def load_teams(conn: sqlite3.Connection, teams: list[dict]) -> None:
    columns = [
        "id", "name", "short_name", "strength",
        "strength_overall_home", "strength_overall_away",
        "strength_attack_home", "strength_attack_away",
        "strength_defence_home", "strength_defence_away",
        "played", "win", "draw", "loss", "points", "position",
    ]
    insert_rows(conn, "teams", teams, columns)


def load_gameweeks(conn: sqlite3.Connection, events: list[dict]) -> None:
    columns = [
        "id", "name", "deadline_time", "finished", "is_current", "is_next",
        "average_entry_score", "highest_score", "most_selected",
        "most_transferred_in", "most_captained", "most_vice_captained", "top_element",
    ]
    insert_rows(conn, "gameweeks", events, columns)


def load_players(conn: sqlite3.Connection, elements: list[dict]) -> None:
    columns = [
        "id", "first_name", "second_name", "web_name", "team_id", "position_id",
        "now_cost", "status", "news", "chance_of_playing_next_round",
        "total_points", "event_points", "points_per_game", "form", "ep_this", "ep_next",
        "selected_by_percent", "value_form", "value_season",
        "minutes", "starts", "goals_scored", "assists", "clean_sheets", "goals_conceded",
        "own_goals", "penalties_saved", "penalties_missed", "yellow_cards", "red_cards",
        "saves", "bonus", "bps", "influence", "creativity", "threat", "ict_index",
        "clearances_blocks_interceptions", "recoveries", "tackles", "defensive_contribution",
        "expected_goals", "expected_assists", "expected_goal_involvements", "expected_goals_conceded",
        "transfers_in", "transfers_out", "transfers_in_event", "transfers_out_event",
    ]
    rows = []
    for el in elements:
        row = dict(el)
        row["team_id"] = el["team"]
        row["position_id"] = el["element_type"]
        rows.append(row)
    insert_rows(conn, "players", rows, columns)


def load_fixtures(conn: sqlite3.Connection, fixtures: list[dict]) -> None:
    columns = [
        "id", "event", "kickoff_time", "team_h", "team_a",
        "team_h_score", "team_a_score", "team_h_difficulty", "team_a_difficulty",
        "started", "finished",
    ]
    insert_rows(conn, "fixtures", fixtures, columns)


def main() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    DB_PATH.unlink(missing_ok=True)

    bootstrap = fetch_json(BOOTSTRAP_URL)
    fixtures = fetch_json(FIXTURES_URL)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    load_positions(conn, bootstrap["element_types"])
    load_teams(conn, bootstrap["teams"])
    load_gameweeks(conn, bootstrap["events"])
    load_players(conn, bootstrap["elements"])
    load_fixtures(conn, fixtures)

    conn.commit()

    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ["positions", "teams", "gameweeks", "players", "fixtures"]
    }
    conn.close()

    print(f"Built database -> {DB_PATH}")
    for table, count in counts.items():
        print(f"  {table}: {count} rows")


if __name__ == "__main__":
    main()
