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
- [x] **xG/xA feature** — used FPL's own `expected_goal_involvements` (already present in the historical dataset for 2022-23 onward) instead of a separate Understat scraper — same signal, no extra scraping infrastructure needed. Missing for 2020-21/2021-22, filled with 0.
- [ ] **Storage layer** — decide how scraped data persists (flat CSV/Parquet files vs. a local SQLite/Postgres DB). A local DB pays off once multiple scrapers need to join on player/team/gameweek keys.

> [!TIP]
> Build each scraper to be idempotent and re-runnable (safe to run every gameweek without duplicating rows). This matters more than it seems now — the whole pipeline will run on a schedule eventually (Phase 5).

> [!WARNING]
> FPL's API is undocumented and can change shape without notice. Wrap every field access defensively and keep raw JSON responses cached on disk, so a schema change breaks loudly (and recoverably) rather than silently corrupting downstream data.

---

## Phase 2 — Feature Engineering

- [x] Rolling form features (points/minutes/ICT index over last 3/5 games), leakage-safe via `shift(1)` so a gameweek's features never see its own outcome. ([`model/train_model.py`](model/train_model.py))
- [x] Fixture difficulty feature (official FPL FDR of the opponent, home/away-aware). Modest but real signal. Congestion (games in last N days) not yet added.
- [x] Continuous opponent team-strength feature (`strength_overall_home`/`_away` from each season's `teams.csv`) — richer than the 1-5 FDR bucket, home/away-aware.
- [x] Minutes/start-probability estimate — `started` (played 60+ mins) rolling 3/5-game rate, as a feature distinct from raw average minutes (separates "nailed starter" from "explosive but rotated"). Not a separate model; folded into the main regressor as a feature.
- [ ] Price-change and ownership-trend features (optional, weak signal but easy to add).
- [x] Merge all sources into one training table keyed by `(player_id, gameweek, season)` — `train_model.load_season()`.

---

## Phase 3 — Prediction Model

- [x] **Baseline model** — mean-points and last-5-gameweek-average baselines, to sanity-check everything downstream against before trusting the ML model. ([`model/train_model.py`](model/train_model.py))
- [ ] **Benchmark against `ep_next`** — FPL's own expected-points figure is a free baseline; not available in the historical CSV dataset, so this needs live-season data to compare against (Phase 1's gameweek-history scraper).
- [x] **Train a gradient-boosted model** (`sklearn.GradientBoostingRegressor`) on 2020-21 → 2024-25 (5 seasons, ~130k rows).
- [x] **Backtest** against the full 2025-26 season, held out entirely from training. Beats both baselines: MAE 1.00 vs. 1.58 (mean) / 1.06 (last-5-avg); correlation 0.57 vs. 0.50. See `data/backtest_2025-26_predictions.csv`. (Adding xG/team-strength/start-rate features barely moved this — see the Phase 4 regression note below.)
- [ ] **Retraining cadence** — decide how often the model refits (weekly, during the season, is standard).

> [!IMPORTANT]
> Evaluate the model on **held-out future gameweeks**, never on data it trained on. FPL prediction is a time-series problem — a random train/test split will silently leak future information and produce misleadingly good results.

---

## Phase 4 — Optimization Engine

- [x] Implement the core **ILP squad selector**: given predicted points + budget/formation/club-limit constraints, output the best legal 15-man squad and starting XI + captain. ([`model/optimizer.py`](model/optimizer.py), via `scipy.optimize.milp`)
- [x] Extend to **multi-gameweek transfer planning** — re-solved every gameweek across a full season simulation, not single-gameweek-only. Squad-construction decisions (initial squad, wildcard, transfers) value players over the next 5 gameweeks (frozen current form + each future week's already-published fixture/difficulty — schedule facts, not result lookahead), not just the immediate week. ([`model/simulate_season.py`](model/simulate_season.py))
- [x] Add **transfer-hit logic** (-4 points) — searches 0..min(free transfers + 2, 5) transfers each week and only takes hits when the net *lookahead* gain outweighs the cost, with an exponential confidence discount (`LOOKAHEAD_DECAY = 0.85` per week ahead) so distant, less-trustworthy predictions can't inflate a hit's apparent value.
- [x] Add **chip-timing logic** for all four chips:
  - **Wildcard** — dynamic: triggered the first gameweek within a per-half window (GW6-10 / GW17-21) where a full squad reoptimization beats the best normal transfer by `WC_TRIGGER_MARGIN`, falling back to the window's last gameweek if never triggered.
  - **Bench Boost** — the gameweek immediately after Wildcard fires, when the whole squad is freshly optimized.
  - **Free Hit** — triggered when `FREE_HIT_BLANK_THRESHOLD` (3) or more of the current squad have no fixture that gameweek; a one-week-only reoptimization that reverts next gameweek.
  - **Triple Captain** — online threshold rule, "best striker, easy fixture" (unchanged from before).
- [x] Validate every output squad against [FantasyRules.md](FantasyRules.md) constraints — enforced directly as ILP constraints, not checked after the fact.

> [!IMPORTANT]
> **Full-season backtest results** (model trained only on 2020-21 → 2024-25, zero knowledge of 2025-26 results):
>
> | Version | Score |
> | --- | --- |
> | Single-gameweek-only transfer decisions | 1872 |
> | + 5-gameweek lookahead, no confidence discount | 2055 |
> | + 5-gameweek lookahead with confidence discount (`LOOKAHEAD_DECAY = 0.85`) | **2058** (best so far) |
> | + richer features (xG involvement, opponent strength, start-rate), same fixed chip weeks | 1980 |
> | + dynamic Wildcard timing + Free Hit chip (current code) | **1906** |
>
> Real 2025-26 average manager's actual total: **1895** (sum of `average_entry_score` from `data/fpl.db`) — the current code still beats it, but by less than the 2058 checkpoint did.
>
> **This is a regression, reported honestly rather than hidden.** Both additions (richer features, dynamic chip timing) independently made the score *worse* on this one season, isolated via a diagnostic run (new features + old fixed chip schedule alone scored 1980, confirming the richer features accounted for most of the drop before dynamic chip timing was even added). Two live hypotheses, not fully disentangled: (1) the new features are mostly noise here — single-gameweek MAE barely moved (0.991 → 1.000) and that noise compounds across 38 sequential squad-selection decisions; (2) `WC_TRIGGER_MARGIN=8` was picked without tuning, and Free Hit never fired all season (no gameweek had 3+ blanked squad players), so it added complexity with zero payoff this particular season.
>
> The code was kept anyway (per project decision) — Free Hit and dynamic chip timing are correct, real FPL mechanics worth having even though they didn't help this specific backtest, and per the standing rule in this plan, retuning `WC_TRIGGER_MARGIN` or the feature set until the number looks good again would be tuning against single-season noise, not a real fix. **[Phase 6](#phase-6--evaluation--iteration)'s multi-season backtesting is the correct next step before trusting any further tuning of these parameters.**

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
