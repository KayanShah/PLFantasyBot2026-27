> [!NOTE]
> This project is under development and is not yet working
> 
> Please come back soon- our aim is to get everything working and deployed by the start of the PL 2026/27 season



> [!Caution]
> All data currently pulled in this repository is from the last 2025/2026 season, and this season's fantasy API will only launch at the end of July
> 
> Latest update from [Premier League](https://www.premierleague.com/en/news/4679873/all-you-need-to-know-about-changes-to-fpl-for-202627)




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
| [`model/simulate_season.py`](model/simulate_season.py) | Simulates managing a team through a full season gameweek-by-gameweek — transfers, chips, captaincy — using only pre-season-trained predictions. |
| [`model/multi_season_backtest.py`](model/multi_season_backtest.py) | Runs the simulation across multiple seasons (each trained only on strictly earlier seasons) and compares against real average-manager totals. |
| [`website/build_site.py`](website/build_site.py) / [`website/index.html`](website/index.html) | Builds a self-contained, single-file website showing the bot's team for every 2025-26 gameweek — pitch view, captaincy, chips, difficulty-coded fixtures — scrollable gameweek by gameweek. |
| `data/` | Output from the scrapers (`fixtures.csv`, `fixtures.json`, `fpl.db`, `historical/`, `backtest_2025-26_predictions.csv`, `season_2025-26_simulation.csv`, `season_2025-26_squads.json`, `multi_season_backtest_results.csv`). |
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

Picks a legal GW1 squad from scratch, then goes gameweek-by-gameweek making transfers (respecting free-transfer rollover and -4 hits), playing Wildcard/Bench Boost/Triple Captain at sensible points, and auto-subbing players who didn't play — using only predictions built from data available *before* each gameweek. Saves a gameweek-by-gameweek log to `data/season_2025-26_simulation.csv`, and full squad detail (every player, opponent, difficulty, captaincy) to `data/season_2025-26_squads.json`.

View the team pick for every gameweek in a browser:

```bash
python3 website/build_site.py
open website/index.html
```

Builds a single self-contained HTML file (no server needed) from `data/season_2025-26_squads.json` — a pitch-view layout you scroll through gameweek by gameweek, showing every starter and bench player with their opponent, fixture-difficulty colour coding, captain (C)/vice-captain (V)/triple-captain (3x) badges, and points scored. Rebuild it any time after re-running `simulate_season.py`.

Validate across multiple seasons at once:

```bash
python3 model/multi_season_backtest.py
```

Runs the full pipeline against 2023-24, 2024-25, and 2025-26, each trained *only* on seasons strictly before it (no leakage), and compares each result to that season's real average-manager total. Saves results to `data/multi_season_backtest_results.csv`.

## Results

The model is trained only on seasons strictly before the one it's tested on — it has zero knowledge of the test season's results. `simulate_season.py` manages a team through a real season gameweek-by-gameweek, scored against what actually happened.

**Single-season backtest (2025-26), showing how the approach was built up:**

| Version | Score |
| --- | --- |
| Single-gameweek-only transfer decisions | 1872 |
| + 5-gameweek lookahead, no confidence discount | 2055 |
| **+ 5-gameweek lookahead with confidence discount** (`LOOKAHEAD_DECAY = 0.85`) — current code | **2058** |

**How the lookahead works:** squad-construction decisions (initial squad, wildcard, transfers) value each player by summing their projected points over the next 5 gameweeks, not just the immediate one — so the bot doesn't sell someone right before an easy run of fixtures, or buy into a run of hard ones. Each future week's prediction reuses the player's *current* rolling-form features (frozen — no peeking at results that haven't happened yet) combined with that future week's *already-published* fixture (home/away, FDR difficulty), which is public knowledge from the fixture list, not a result. Each week further out is also discounted since a prediction 4 weeks out is less trustworthy than this week's — so a `-4` transfer hit needs a clearer, closer-in payoff to be worth taking. Starting XI and captaincy stay single-gameweek on purpose — you always want your best lineup *this* week regardless of the run of form ahead.

**Multi-season validation** — the real test, since one season is a single noisy data point:

| Season | Bot | Real avg. manager | Diff |
| --- | --- | --- | --- |
| 2023-24 | 2098 | 2003 | +95 |
| 2024-25 | 2016 | 2008 | +8 |
| 2025-26 | 1991 | 1895 | +96 |

Consistently above the real average manager across three independent seasons, not just a lucky one. (Past seasons' average-manager totals came from [Wayback Machine](https://web.archive.org/) snapshots of `bootstrap-static`, since the live FPL API only serves the current season — see `plan.md` Phase 4 for the exact snapshot URLs.)

> [!NOTE]
> A richer-features + dynamic-chip-timing experiment (xG involvement, opponent team-strength, start-rate, dynamic Wildcard timing, a Free Hit chip) was tried and **regressed** the 2025-26 score to 1906. Rather than keep tuning parameters until the number looked good again on that one season, it was reverted back to the validated 2058 checkpoint above. The experiment is preserved in git history if worth revisiting — ideally with multi-season validation from the start next time.
>
> Several more attempts using real injury/suspension data (starting-XI filters, a model feature, a transfer-value discount) were also tried and reverted — see `plan.md` Phase 4 for all eight. A real bug was also found and fixed along the way: Bench Boost and Triple Captain were being simulated as 2-per-season for every year tested, but that's only true from 2025/26 onward — every earlier season only had 1 of each. Three more bugs were fixed in a later pass: a cold-start bug where players with no rolling-form history (promoted-club players, fresh transfers) predicted near-zero instead of an average-for-position prior; a sell-price bug where the budget model gave full credit for a player's price rise instead of FPL's real half-profit-on-sale rule; and a player-identity bug where rolling form was carried across the season boundary by matching on name string, which isn't stable (a player's own recorded name format can change season to season) — fixed by joining on FPL's actual permanent player `code` instead.

---

> [!IMPORTANT]
> A follow-up investigation found the sell-price fix introduced a serious reliability problem: GW1/Wildcard squad-rebuild decisions turned out to be decided by sub-1-point margins between hundreds of near-tied 15-player combinations, and the sell-price fix's budget path-dependency let that tiny, essentially arbitrary noise compound into 100+ point season-total swings — the *same* bot, same skill, landing anywhere from 1960 to 2172 points in 2023-24 purely by chance. Mitigated two ways: averaging a 5-model prediction ensemble for GW1/Wildcard squad construction specifically (a genuine but partial fix — some pairs of models still land on different sides of a tie even after averaging, confirmed via before/after seed sweep), and a stability margin on ordinary transfer weeks (`TRANSFER_MARGIN`, only take a transfer if it beats holding by more than a set threshold). See `plan.md` Phase 4 for the full writeup, every before/after table, and the one claim (margin can't touch Wildcard decisions) that turned out to need correcting mid-investigation.

---

> [!NOTE]
> Free Hit was isolated and tested standalone for the first time (previously only ever tried bundled with other changes, in the regressed/reverted experiment above). Triggered like Triple Captain — data-driven, not a fixed calendar week: played when a single-gameweek-optimal unconstrained squad clearly beats the current squad's actual best XI that week. Kept as a net positive after checking its per-season swings against each season's known noise floor rather than taking them at face value — see `plan.md` Phase 4 for the numbers.

---

> [!WARNING]
> `TRANSFER_MARGIN` was originally set to 1.0 from a 5-seed sweep, then corrected to **1.5** after two rounds of follow-up scrutiny: (1) re-verifying the sweep surfaced an apparent reproducibility failure that turned out to be Free Hit having been added to the codebase *after* the original sweep ran, silently changing what was being measured — not a bug, but every re-check needed redoing on the current pipeline; (2) redoing it properly (60 runs, 5 seeds × 4 margins × 3 seasons) showed 1.5 has the highest aggregate score across all three seasons and is better in 2 of 3, while 1.0 is safer (better-or-equal in all three) but leaves real points on the table. Chose 1.5 for consistency with how the Free Hit decision above was already made — judged by aggregate performance and "better in most," not a stricter unstated "zero regression anywhere" rule applied only to this one decision. The numbers above are the final corrected figures after all seven fixes/additions and this correction — see `plan.md` Phase 4 for the full writeup, including the exact reproducibility-mystery diagnosis.

See [plan.md](plan.md#phase-4--optimization-engine) for the full breakdown, including the diagnostic that isolated the regression.

## Data sources

- All fixture and player data comes from the official (undocumented) Fantasy Premier League API at `https://fantasy.premierleague.com/api/`. See [research.md](research.md#21-official-fantasy-premier-league-api-free-no-key-required) for the full endpoint reference.
- Historical season data (2020-21 → 2025-26) is from [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League).
- Real, point-in-time historical player injury/availability data — used in training and for the starting-XI/captaincy availability filter — is from [Randdalf/fplcache](https://github.com/Randdalf/fplcache), which has archived FPL's live API 4x/day since April 2021. Without this project, the historical simulation would have no way to know what was actually known about a player's fitness before each gameweek's deadline, only what happened after.
