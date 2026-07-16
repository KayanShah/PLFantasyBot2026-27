> [!NOTE]
> This project is under development and is not yet working
> 
> Please come back soon- our aim is to get everything working and deployed by the start of the PL 2026/27 season



<p align="center">
  <img src="https://fantasy.premierleague.com/img/favicons/apple-touch-icon.png" width="120" alt="Fantasy Premier League logo" />
</p>

<h1 align="center">PLFantasyBot 2026/27</h1>

<p align="center">
  A bot that builds the best possible Fantasy Premier League team using historical data, live stats, and predictive modeling.
</p>

---

## What this is

FPL team selection is fundamentally two problems:

1. **Predict** how many points each player will score.
2. **Optimize** squad, captain, and transfer choices under FPL's budget and formation rules.

This project pulls data from the official FPL API and other sources, predicts player performance, and uses optimization to pick the best legal squad — see [research.md](research.md) for the full write-up of data sources, modeling approaches, and the optimization strategy this is built on.

## Repo layout

| Path | Description |
|---|---|
| [`research.md`](research.md) | Research notes: FPL API endpoints, data sources, prediction approaches, optimization strategy. |
| [`FantasyRules.md`](FantasyRules.md) | Official FPL rules: squad/budget constraints, scoring system, transfers, chips. |
| [`plan.md`](plan.md) | Ordered build plan from data collection through to a fully automated bot. |
| [`scrapers/scrape_fixtures.py`](scrapers/scrape_fixtures.py) | Scrapes all season fixtures (teams, kickoff times, difficulty ratings, scores) from the FPL API. |
| [`scrapers/build_database.py`](scrapers/build_database.py) | Builds `data/fpl.db`, a full SQLite database of teams, players, gameweeks, and fixtures. |
| [`model/fetch_historical_data.py`](model/fetch_historical_data.py) | Downloads past-season gameweek data (2020-21 → 2025-26) for model training and backtesting. |
| [`model/train_model.py`](model/train_model.py) | Trains a points-prediction model on 2020-21 → 2024-25 and backtests it against the held-out 2025-26 season. |
| [`model/optimizer.py`](model/optimizer.py) | ILP squad selector + starting-XI/captain picker, enforcing every constraint in `FantasyRules.md`. |
| [`model/simulate_season.py`](model/simulate_season.py) | Simulates managing a team through the full 2025-26 season gameweek-by-gameweek — transfers, chips, captaincy — using only pre-season-trained predictions. |
| `data/` | Output from the scrapers (`fixtures.csv`, `fixtures.json`, `fpl.db`, `historical/`, `backtest_2025-26_predictions.csv`, `season_2025-26_simulation.csv`). |
| `requirements.txt` | Python dependencies. |

## Setup

```bash
pip install -r requirements.txt
```

## Usage

Scrape the current season's fixtures:

```bash
python3 scrapers/scrape_fixtures.py
```

Outputs `data/fixtures.csv` and `data/fixtures.json`.

Build the full SQLite database:

```bash
python3 scrapers/build_database.py
```

Outputs `data/fpl.db` — open it directly in [DB Browser for SQLite](https://sqlitebrowser.org/) to explore. Tables: `positions`, `teams`, `gameweeks`, `players`, `fixtures`, all linked by foreign keys (`players.team_id → teams.id`, `players.position_id → positions.id`, `fixtures.team_h`/`team_a → teams.id`, `fixtures.event → gameweeks.id`).

Fetch historical seasons and train/backtest the points-prediction model:

```bash
python3 model/fetch_historical_data.py
python3 model/train_model.py
```

Trains a gradient-boosted model on 2020-21 → 2024-25 and backtests it against the full 2025-26 season (never seen during training), printing MAE/RMSE/correlation against baselines and saving per-gameweek predictions to `data/backtest_2025-26_predictions.csv`.

Simulate managing a real team through the entire 2025-26 season:

```bash
python3 model/simulate_season.py
```

Picks a legal GW1 squad from scratch, then goes gameweek-by-gameweek making transfers (respecting free-transfer rollover and -4 hits), playing Wildcard/Bench Boost/Triple Captain at sensible points, and auto-subbing players who didn't play — using only predictions built from data available *before* each gameweek. Scores against the real 2025-26 results. First run: **1872 points**, vs. the real season's average-manager total of **1895** (see `plan.md` Phase 4 for the full breakdown). Saves a gameweek-by-gameweek log to `data/season_2025-26_simulation.csv`.

## Data source

All fixture and player data comes from the official (undocumented) Fantasy Premier League API at `https://fantasy.premierleague.com/api/`. See [research.md](research.md#21-official-fantasy-premier-league-api-free-no-key-required) for the full endpoint reference.
