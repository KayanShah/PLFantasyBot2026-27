# Project Plan

A logical, ordered build plan for PLFantasyBot, from raw data to a fully automated squad-picking bot. Each phase builds on the last — see [research.md](research.md) for the reasoning behind these choices and [FantasyRules.md](FantasyRules.md) for the constraints the optimizer must respect.

> [!NOTE]
> This plan is sequential by design: a prediction model is only as good as its data, and an optimizer is only as good as its predictions. Don't skip ahead to optimization with fake/placeholder point projections and expect the results to mean anything.

---

> [!CAUTION]
> We are currently awaiting 2026/27 season data. Everything in `data/` right now (`fixtures.csv`, `fixtures.json`, `fpl.db`) is **2025/26 season data** — the FPL API hasn't reset for the new season yet. Do not build or trust any model/optimization output against the current dataset; re-run the scrapers once FPL officially launches 2026/27 (expected mid-to-late July 2026).

---

## Phase 0 — Foundations ✅

- [x] Research data sources and modeling approach ([research.md](research.md))
- [x] Document official FPL rules and scoring ([FantasyRules.md](FantasyRules.md))
- [x] Fixture scraper ([`scrapers/scrape_fixtures.py`](scrapers/scrape_fixtures.py))

---

## Phase 1 — Data Collection

- [ ] **Player scraper** — pull `bootstrap-static` into a clean player table: id, name, team, position, price, ownership, current-season totals, ICT index, `ep_next`.
- [ ] **Gameweek history scraper** — per-player, per-gameweek point breakdowns via `element-summary/{id}/`, so the model has granular training rows, not just season totals.
- [x] **Historical seasons loader** — ingest [vaastav's historical dataset](https://github.com/vaastav/Fantasy-Premier-League) so the model trains on multiple past seasons, not just the current one. ([`model/fetch_historical_data.py`](model/fetch_historical_data.py))
- [ ] **xG/xA feature** — tried using FPL's own `expected_goal_involvements` (2022-23 onward) instead of a separate Understat scraper, reverted along with the Phase 4 regression. The "use FPL's own field, skip scraping Understat" approach is still sound; worth retrying with multi-season validation.
- [ ] **Storage layer** — decide how scraped data persists (flat CSV/Parquet files vs. a local SQLite/Postgres DB). A local DB pays off once multiple scrapers need to join on player/team/gameweek keys.

> [!TIP]
> Build each scraper to be idempotent and re-runnable (safe to run every gameweek without duplicating rows). This matters more than it seems now — the whole pipeline will run on a schedule eventually (Phase 5).

> [!WARNING]
> FPL's API is undocumented and can change shape without notice. Wrap every field access defensively and keep raw JSON responses cached on disk, so a schema change breaks loudly (and recoverably) rather than silently corrupting downstream data.

---

## Phase 2 — Feature Engineering

- [x] Rolling form features (points/minutes/ICT index over last 3/5 games), leakage-safe via `shift(1)` so a gameweek's features never see its own outcome. ([`model/train_model.py`](model/train_model.py))
- [x] Fixture difficulty feature (official FPL FDR of the opponent, home/away-aware). Modest but real signal. Congestion (games in last N days) not yet added.
- [ ] Continuous opponent team-strength feature — tried (`strength_overall_home`/`_away`), reverted along with the Phase 4 regression (see below); worth retrying with multi-season validation before committing to it.
- [ ] Minutes/start-probability estimate — tried as a `started`-rate feature, reverted along with the Phase 4 regression; a *separate* dedicated model (rather than folding it into the main regressor) is still untried and may work better.
- [ ] Price-change and ownership-trend features (optional, weak signal but easy to add).
- [x] Merge all sources into one training table keyed by `(player_id, gameweek, season)` — `train_model.load_season()`.

---

## Phase 3 — Prediction Model

- [x] **Baseline model** — mean-points and last-5-gameweek-average baselines, to sanity-check everything downstream against before trusting the ML model. ([`model/train_model.py`](model/train_model.py))
- [ ] **Benchmark against `ep_next`** — FPL's own expected-points figure is a free baseline; not available in the historical CSV dataset, so this needs live-season data to compare against (Phase 1's gameweek-history scraper).
- [x] **Train a gradient-boosted model** (`sklearn.GradientBoostingRegressor`) on 2020-21 → 2024-25 (5 seasons, ~130k rows).
- [x] **Backtest** against the full 2025-26 season, held out entirely from training. Beats both baselines: MAE 0.99 vs. 1.58 (mean) / 1.06 (last-5-avg); correlation 0.57 vs. 0.50. See `data/backtest_2025-26_predictions.csv`. Now validated across 3 seasons, not just this one — see the Phase 4 multi-season backtest.
- [ ] **Retraining cadence** — decide how often the model refits (weekly, during the season, is standard).

> [!IMPORTANT]
> Evaluate the model on **held-out future gameweeks**, never on data it trained on. FPL prediction is a time-series problem — a random train/test split will silently leak future information and produce misleadingly good results.

---

## Phase 4 — Optimization Engine

- [x] Implement the core **ILP squad selector**: given predicted points + budget/formation/club-limit constraints, output the best legal 15-man squad and starting XI + captain. ([`model/optimizer.py`](model/optimizer.py), via `scipy.optimize.milp`)
- [x] Extend to **multi-gameweek transfer planning** — re-solved every gameweek across a full season simulation, not single-gameweek-only. Squad-construction decisions (initial squad, wildcard, transfers) value players over the next 5 gameweeks (frozen current form + each future week's already-published fixture/difficulty — schedule facts, not result lookahead), not just the immediate week. ([`model/simulate_season.py`](model/simulate_season.py))
- [x] Add **transfer-hit logic** (-4 points) — searches 0..min(free transfers + 2, 5) transfers each week and only takes hits when the net *lookahead* gain outweighs the cost, with an exponential confidence discount (`LOOKAHEAD_DECAY = 0.85` per week ahead) so distant, less-trustworthy predictions can't inflate a hit's apparent value.
- [x] Add **chip-timing logic** for Wildcard / Bench Boost / Triple Captain (fixed heuristic weeks for WC/BB, online threshold rule for Triple Captain — "best striker, easy fixture"). **Free Hit is not simulated** — tried and reverted (see below).
- [x] Validate every output squad against [FantasyRules.md](FantasyRules.md) constraints — enforced directly as ILP constraints, not checked after the fact.

> [!IMPORTANT]
> **Full-season backtest results, 2025-26** (model trained only on 2020-21 → 2024-25, zero knowledge of 2025-26 results):
>
> | Version | Score |
> | --- | --- |
> | Single-gameweek-only transfer decisions | 1872 |
> | + 5-gameweek lookahead, no confidence discount | 2055 |
> | **+ 5-gameweek lookahead with confidence discount (`LOOKAHEAD_DECAY = 0.85`)** — current code | **2058** |
>
> Real 2025-26 average manager's actual total: **1895** (sum of `average_entry_score` from `data/fpl.db`).

---

> [!IMPORTANT]
> **Multi-season validation** (`model/multi_season_backtest.py`, results in `data/multi_season_backtest_results.csv`): the 2058-checkpoint code was tested against three independent seasons, each trained *only* on seasons strictly before it (no leakage):
>
> | Season | Bot | Avg Manager | Diff |
> | --- | --- | --- | --- |
> | 2023-24 | 2056 | 2003 | +53 |
> | 2024-25 | 2149 | 2008 | +141 |
> | 2025-26 | 2058 | 1895 | +163 |
>
> Average-manager totals for past seasons aren't available from the live FPL API once a season ends (it only serves the current season), so these were pulled from [Wayback Machine](https://web.archive.org/) snapshots of `bootstrap-static` taken at each season's end: [2022-23](http://web.archive.org/web/20230611030006/https://fantasy.premierleague.com/api/bootstrap-static/), [2023-24](http://web.archive.org/web/20240521000009/https://fantasy.premierleague.com/api/bootstrap-static/), [2024-25](http://web.archive.org/web/20250612210134/https://fantasy.premierleague.com/api/bootstrap-static/); summed each season's `events[].average_entry_score`.
>
> **This is the real validation the earlier single-season caution was waiting for** — the approach beats the average manager consistently across three independent seasons, not just a lucky one.

---

> [!WARNING]
> **A richer-features + dynamic-chip-timing experiment was tried and reverted.** Adding xG involvement, opponent team-strength, start-rate features, a dynamic Wildcard trigger, and a Free Hit chip *regressed* the 2025-26 score to 1906 (features alone: 1980; + dynamic chips: 1906) — worse than the 2058 checkpoint above on that one season. Rather than keep tuning parameters like `WC_TRIGGER_MARGIN` until the number looked good again (tuning against single-season noise), the code was reverted to the validated 2058 checkpoint. The experiment is preserved in git history (commit `9282fbc` onward) if worth revisiting — ideally validated across multiple seasons like the checkpoint above, not just one, before being trusted.

---

> [!WARNING]
> **A second attempt — full xG/xA/xG-conceded/starts/saves features, chip timing left untouched this time to isolate the cause — was also tried and reverted, this time validated across all 3 seasons from the start:**
>
> | Season | Baseline (validated) | + xG/xA/starts/saves |
> | --- | --- | --- |
> | 2023-24 | 2056 (+53 vs avg) | 1956 (**-47** vs avg) |
> | 2024-25 | 2149 (+141 vs avg) | 1957 (**-51** vs avg) |
> | 2025-26 | 2058 (+163 vs avg) | 1912 (+17 vs avg) |
>
> This regressed **consistently across all three independent seasons** — a much stronger, more confident negative result than the first attempt, since it isn't attributable to single-season noise. Most likely explanation: xG/xA/starts are highly correlated with signals the model already derives from `ict_index`/`threat`/`creativity`/`minutes`, and adding more correlated-but-noisier columns diluted the model rather than sharpening it. Reverted; not on `main`.
>
> **Takeaway for future attempts:** "more stats" isn't automatically better for a tree-based model already using strong composite features (ICT index already blends a lot of this signal) — the real gap is more likely in the *structure* of the model (e.g. a genuinely separate minutes/rotation-risk model, not just another input column) than in adding more raw columns to the existing one.

---

> [!TIP]
> [sertalpbilal/FPL-Optimization-Tools](https://github.com/sertalpbilal/FPL-Optimization-Tools) (HiGHS solver via `sasoptpy`) remains a good reference for going further — e.g. true rolling-horizon lookahead (planning transfers *ahead* of the gameweek they're needed) rather than this project's greedy week-by-week approach.

---

## Phase 5 — Automation & Interface

- [ ] **Scheduled pipeline** — scrape → feature-build → predict → optimize, run automatically each gameweek before the transfer deadline.
- [ ] **Output/reporting** — a simple weekly report (console output, file, or notification) showing recommended transfers, captain, and chip usage with reasoning.
- [ ] *(Optional)* **Live team sync** — authenticate against your own FPL team via `/my-team/{manager_id}/` to compare the bot's recommendation against your actual squad.
- [ ] *(Optional)* **Auto-apply transfers** — only if you're comfortable letting the bot act without a manual approval step; recommend keeping a human-in-the-loop confirmation initially.

> [!WARNING]
> Auto-applying transfers on your live FPL team is an irreversible action each gameweek. Keep a manual review/approval step until the model and optimizer have a season's worth of backtested trust behind them.

---

## Phase 6 — Evaluation & Iteration

- [ ] Track the bot's actual gameweek-by-gameweek score against a real season, not just backtests.
- [ ] Compare against benchmarks: FPL average score, `ep_next`-only strategy, and top public FPL AI tools (e.g. OpenFPL).
- [ ] Iterate on features/model based on where predictions miss most (e.g. rotation risk, red cards, injuries).

---

## Summary — Build Order

```
Data Collection  →  Feature Engineering  →  Prediction Model  →  Optimization Engine  →  Automation  →  Evaluation
   (Phase 1)            (Phase 2)              (Phase 3)             (Phase 4)            (Phase 5)      (Phase 6)
```

> [!NOTE]
> It's fine — even good — to build a thin, end-to-end version of every phase first (dummy data → dummy predictions → working optimizer), then go back and deepen each phase, rather than perfecting Phase 1 before touching Phase 2. That gets a working prototype fast and de-risks the integration points early.
