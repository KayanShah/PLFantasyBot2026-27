# Project Plan

A logical, ordered build plan for PLFantasyBot, from raw data to a fully automated squad-picking bot. Each phase builds on the last — see [research.md](research.md) for the reasoning behind these choices and [FantasyRules.md](FantasyRules.md) for the constraints the optimizer must respect.

Want to contribute to the plan? see [CONTRIBUTING.md](CONTRIBUTING.md)

> [!NOTE]
> This plan is sequential by design: a prediction model is only as good as its data, and an optimizer is only as good as its predictions. Don't skip ahead to optimization with fake/placeholder point projections and expect the results to mean anything.

---

> [!CAUTION]
> **The FPL API has now reset for 2026/27** — 38 gameweeks, 563 players, GW1 deadline `2026-08-21T17:30Z`, with Coventry/Hull/Ipswich promoted and Burnley/Wolves relegated. [`model/live_pipeline.py`](model/live_pipeline.py) reads it directly.
>
> The committed files in `data/` (`fixtures.csv`, `fixtures.json`, `fpl.db`) are **still 2025/26 data** — the scrapers haven't been re-run since the reset. Don't trust those specific files for 2026/27 until they are.

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
- [x] **Historical injury/availability data** — the vaastav dataset has none (it records what *happened*, not what was known beforehand); fetched real point-in-time `status`/`chance_of_playing` from [Randdalf/fplcache](https://github.com/Randdalf/fplcache)'s 4x-daily archive of FPL's live API. ([`model/fetch_availability_data.py`](model/fetch_availability_data.py)) — feature itself tried and reverted (see Phase 4), but the data and fetcher are kept as a foundation.
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

> [!NOTE]
> **Investigated whether the model's predictions are too compressed to separate good players from mediocre ones — they aren't, and the investigation ended up disproving its own starting hypothesis. No code changed.**
>
> **The observation that started it.** Sweeping `total_points_avg3` with every other feature held at a fixed player-gameweek shows the model is *flat* across the range where most decisions live:
>
> | recent form | 0 | 1 | **2** | **3** | **4** | **5** | **6** | 8 | 10 |
> | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
> | predicted | 4.92 | 5.91 | **6.36** | **6.36** | **6.36** | **6.36** | **6.36** | 6.46 | 6.46 |
>
> A player averaging 2 points and one averaging 6 get an identical prediction. Switching home→away moves the prediction −0.52, more than a four-point swing in form does. Predicted spread is also far tighter than reality (sd 1.28 vs 2.39; p99 4.54 vs 11.00). The initial theory was that this compression starves premium players under a budget constraint, and explained why the bot went whole seasons without owning the highest-ceiling forward.
>
> **Hypothesis 1 — the 58% of training rows with zero minutes eat the tree splits. Falsified.** Retraining on `minutes > 0` rows only made resolution *worse*, not better: the sweep's span fell from 1.54 to 0.53, with more flat stretches, not fewer.
>
> **Hypothesis 2 — it's an artifact of squared-error loss on a skewed target. Falsified.** Four model families, same features, same data:
>
> | model | MAE | pearson | spearman | sd | top-15 hit rate | sweep span |
> | --- | --- | --- | --- | --- | --- | --- |
> | GBR squared-error (current) | 1.009 | 0.564 | 0.703 | 1.28 | 15.1% | 1.54 |
> | GBR depth 5 | 1.007 | 0.562 | 0.703 | 1.30 | 16.0% | 0.87 |
> | HistGBR Poisson | 1.013 | 0.560 | 0.701 | 1.30 | 14.9% | 2.66 |
> | HistGBR squared-error | 1.011 | 0.560 | 0.702 | 1.29 | 14.6% | 1.51 |
>
> Statistically indistinguishable on every metric that drives squad choice, and *all four* are flat over the same form range — including Poisson, which suits skewed count data. The flat zone is a property of the data, not of the model family: past this threshold, recent points form genuinely stops carrying information once the other features are known.
>
> **The decisive test — calibration, which reversed the conclusion outright.** Under-dispersion only distorts squad selection if the model is *miscalibrated*. It isn't. Predicted deciles track actuals within −0.07 to +0.25 across the whole pool. And by price tier, restricted to `pred > 1.5` so bench fodder doesn't dominate:
>
> | tier | predicted | actual | actual/predicted |
> | --- | --- | --- | --- |
> | <5.0m | 2.409 | 2.678 | **1.112** |
> | 5.0-6.5m | 2.716 | 2.807 | 1.034 |
> | 6.5-8.0m | 3.384 | 3.378 | 0.998 |
> | 8.0-10.0m | 3.733 | 3.682 | 0.986 |
> | >10.0m | 4.752 | 4.411 | **0.928** |
>
> The model **over-values premiums and under-values cheap players** — the exact opposite of the compression theory. A recalibration would push the bot *further away* from expensive players, not toward them.
>
> **What this means for the "best player never owned" problem.** It is not a prediction problem. The predictions already rate premiums generously. Whatever keeps a high-ceiling forward out of the squad for most of a season lives in the *transfer mechanics* — one free transfer a week cannot reach a £14m striker except at a full rebuild — which points at transfer planning and chip pre-positioning, not the model.
>
> **Nothing shipped**, deliberately: no model change survived contact with the evidence, and a recalibration sized at ratio 0.93-1.11 would be far below this pipeline's ±15-125 noise floor even if its direction were desirable, which it isn't. Recorded here so the next person doesn't re-derive it — the sweep looks alarming, and it is genuinely tempting to spend a week on it.

---

## Phase 4 — Optimization Engine

- [x] Implement the core **ILP squad selector**: given predicted points + budget/formation/club-limit constraints, output the best legal 15-man squad and starting XI + captain. ([`model/optimizer.py`](model/optimizer.py), via `scipy.optimize.milp`)
- [x] Extend to **multi-gameweek transfer planning** — re-solved every gameweek across a full season simulation, not single-gameweek-only. Squad-construction decisions (initial squad, wildcard, transfers) value players over the next 5 gameweeks (frozen current form + each future week's already-published fixture/difficulty — schedule facts, not result lookahead), not just the immediate week. ([`model/simulate_season.py`](model/simulate_season.py))
- [x] Add **transfer-hit logic** (-4 points) — searches 0..min(free transfers + 2, 5) transfers each week and only takes hits when the net *lookahead* gain outweighs the cost, with an exponential confidence discount (`LOOKAHEAD_DECAY = 0.85` per week ahead) so distant, less-trustworthy predictions can't inflate a hit's apparent value.
- [x] Add **chip-timing logic** for Wildcard / Bench Boost / Triple Captain (fixed heuristic weeks for WC/BB, online threshold rule for Triple Captain — "best striker, easy fixture"), **season-aware since a real bug fix**: Wildcard has always been 2/season, but Bench Boost and Triple Captain only became 2/season (one per half) from 2025/26 onward — every earlier season only had 1 of each for the whole season. Simulating 2/each for older seasons was a real bug that inflated their results (see the correction note below). **Free Hit is not simulated** — tried and reverted (see below).
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
> | 2023-24 | 2040 | 2003 | +37 |
> | 2024-25 | 2118 | 2008 | +110 |
> | 2025-26 | 2058 | 1895 | +163 |
>
> Average-manager totals for past seasons aren't available from the live FPL API once a season ends (it only serves the current season), so these were pulled from [Wayback Machine](https://web.archive.org/) snapshots of `bootstrap-static` taken at each season's end: [2022-23](http://web.archive.org/web/20230611030006/https://fantasy.premierleague.com/api/bootstrap-static/), [2023-24](http://web.archive.org/web/20240521000009/https://fantasy.premierleague.com/api/bootstrap-static/), [2024-25](http://web.archive.org/web/20250612210134/https://fantasy.premierleague.com/api/bootstrap-static/); summed each season's `events[].average_entry_score`.
>
> **This is the real validation the earlier single-season caution was waiting for** — the approach beats the average manager consistently across three independent seasons, not just a lucky one.
>
> **Correction (see below the 8 reverted experiments for the full writeup):** the 2023-24/2024-25 numbers above were originally 2056/2149 — both inflated by a real bug where the simulator gave the bot 2 Bench Boosts and 2 Triple Captains in seasons that historically only allowed 1 of each for the whole season (that rule only became 2-per-season from 2025/26 onward). Fixed; these are the corrected, accurate numbers. The bot still beats the average manager in every season under the correct rules, just by a smaller margin in the two older ones.

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

> [!WARNING]
> **A third attempt — full-season lookahead (every remaining gameweek, not just 5) + double-gameweek-aware Triple Captain + an availability proxy to avoid starting/captaining low-recent-minutes players — was tried and reverted**, validated across all 3 seasons:
>
> | Season | Baseline (validated) | + full lookahead / DGW-TC / availability |
> | --- | --- | --- |
> | 2023-24 | 2056 (+53 vs avg) | 2160 (**+157** vs avg) |
> | 2024-25 | 2149 (+141 vs avg) | 1953 (**-55** vs avg — below average) |
> | 2025-26 | 2058 (+163 vs avg) | 2025 (+130 vs avg) |
>
> Mixed and net negative: better in 2023-24 by a real margin (+104), but worse in the other two, and 2024-25 actually dropped below the real average manager — 2 of 3 seasons regressed, and the total across all three fell (6263 → 6138). Per this project's own rule (commit only if better-or-equal across most seasons), reverted; not on `main`.
>
> Three changes were bundled together in this attempt, so the specific cause isn't isolated — a mistake this plan has flagged before (see the first reverted experiment above) and repeated here under time pressure. The Triple Captain fix along the way *is* correct and worth knowing regardless of the overall revert: the first version incorrectly fired the chip before a double gameweek arrived; the corrected version makes the bot wait, since the fixture schedule (unlike results) is legitimately public knowledge in advance — this logic is sound, just bundled with two other changes that muddy attribution of the net result.
>
> **Takeaway:** the 2023-24 improvement is a real, interesting signal that full-season lookahead *can* help — but bundling it with the DGW-TC fix and the availability proxy makes it impossible to tell whether lookahead, DGW-TC, availability, or some interaction between them is driving the 2024-25/2025-26 regression. Retrying each change in isolation, multi-season-validated one at a time, is the correct next step rather than reverting the whole idea.

---

> [!WARNING]
> **A fourth attempt, isolating the availability piece from the third: real historical injury/availability data** (not a proxy this time — see [`model/fetch_availability_data.py`](model/fetch_availability_data.py), sourced from [Randdalf/fplcache](https://github.com/Randdalf/fplcache), which has archived FPL's live API 4x/day since April 2021) **was fetched, added as a genuine model feature, and used as a hard `<50%`-chance exclusion rule for starting XI/captaincy — also reverted:**
>
> | Season | Baseline (validated) | + real availability data |
> | --- | --- | --- |
> | 2023-24 | 2056 (+53 vs avg) | 1976 (**-80** vs avg — below average) |
> | 2024-25 | 2149 (+141 vs avg) | 2230 (**+222** vs avg) |
> | 2025-26 | 2058 (+163 vs avg) | 2024 (+129 vs avg) |
>
> Single-gameweek prediction accuracy genuinely improved a lot from this feature — MAE 0.991 → **0.942**, the largest gain of any feature tried, with `availability` landing 5th in feature importance. **Checked, not assumed:** splitting the test set into squad-relevant players (top 150 predicted/GW) vs. fringe players showed the MAE gain is real for both groups (+0.067 relevant, +0.045 fringe) — so "it's just free MAE from players nobody would pick anyway" is **not** the explanation; the model is genuinely better once it knows injury status. But the full-season result was still mixed and net negative (6263 → 6230 total, 2 of 3 seasons worse). Reverted; not on `main`. See the fifth attempt below for what actually explains the gap.
>
> **What was kept regardless:** `model/fetch_availability_data.py` and `data/historical/*/availability.csv` — genuine, hard-won historical injury data that didn't exist anywhere in this project before, fetched individually per gameweek (no bulk repo clone) via a single Git Trees API call plus ~190 small on-demand downloads.

---

> [!WARNING]
> **A fifth attempt isolated the two pieces of the fourth: real availability data used *only* as a decision-time filter for starting-XI/captaincy (never fed to the model as a training feature this time)** — so it can't affect predicted points at all, only which of an already-owned 15 gets started:
>
> | Season | Baseline (validated) | + isolated XI/captaincy filter only |
> | --- | --- | --- |
> | 2023-24 | 2056 (+53 vs avg) | 2041 (**-15**) |
> | 2024-25 | 2149 (+141 vs avg) | 2073 (**-76**) |
> | 2025-26 | 2058 (+163 vs avg) | 2058 (**exactly identical**) |
>
> The 2025-26 result being *exactly* unchanged (verified: zero cases of a flagged low-availability player actually being started) revealed something important: the simulation already has two after-the-fact corrections for blanked players — **auto-subs** (any starter with 0 real minutes gets swapped for a bench player who played) and **vice-captain fallback** (if the captain gets 0 minutes, the vice's score is used instead). A *pre-emptive* injury filter mostly duplicates what these reactive mechanisms already do. Where it differs — 2023-24 and 2024-25 — it's a **regression**, because `chance_of_playing < 50%` still means up to a 49% chance the player *does* play; pre-emptively benching them on that probability sometimes swaps away from someone who ends up playing and scoring, into a bench option who then blanks. Auto-subs only ever act on the *certain, real* outcome (0 actual minutes), which is a strictly better signal than a pre-game probability. Reverted; not on `main`.
>
> **This resolves the open question from the fourth attempt:** the fourth attempt's net-negative result isn't fully explained by "double-penalty" as first guessed — this fifth attempt shows the filter alone is *already* mildly negative on its own, before even combining with the model feature. The real opportunity for this data, per both attempts, is informing **transfer/wildcard decisions** (who to own in the first place) rather than XI selection on an already-fixed squad, where the existing auto-sub/vice-captain logic already covers most of the value.

---

> [!WARNING]
> **A sixth attempt raised the exclusion bar** — only bench someone if there's a **>75% chance they're out** (`chance_of_playing < 25`, not `< 50` as in the fifth attempt), on the reasoning that a coin-flip-ish 50% doubt shouldn't override the model's own judgement, only a near-certain absence should:
>
> | Season | Baseline (validated) | 50%-threshold (5th attempt) | 75%-out threshold (6th attempt) |
> | --- | --- | --- | --- |
> | 2023-24 | 2056 | 2041 (-15) | 2044 (**-12**) |
> | 2024-25 | 2149 | 2073 (-76) | 2079 (**-70**) |
> | 2025-26 | 2058 | 2058 (0) | 2058 (**0**) |
>
> Directionally correct — a stricter bar causes less damage, exactly as the fifth attempt's reasoning predicted — but still a net regression overall (6263 → 6181, worse in 2 of 3 seasons). Reverted; not on `main`.
>
> **Where this leaves things:** every threshold tried for a *pre-emptive, decision-time-only* XI/captaincy filter has been neutral-to-negative, because it's competing against a strictly better signal the simulation already has for free — the *actual, certain* outcome via auto-subs and vice-captain fallback. Tightening the threshold further would presumably keep approaching (but not exceed) the baseline as it excludes fewer and fewer players — confirmed directly by the seventh attempt below.

---

> [!NOTE]
> **A seventh attempt tested the limiting case of attempt six's own logic: a signal that's (almost) never wrong instead of just "less wrong."** A red card is a near-certain suspension next gameweek — verified against real data first, not assumed: 79-96% of red-carded players got 0 minutes the following gameweek across three separate seasons (53/55 in 2023-24 alone). Unlike the probabilistic `chance_of_playing` field, this needed no external data at all — `red_cards` was already in the historical dataset from day one. Applied the same way as attempts 5-6 (decision-time-only, never fed to the model): never start or captain someone sent off last gameweek.
>
> | Season | Baseline (validated) | + red-card suspension filter |
> | --- | --- | --- |
> | 2023-24 | 2056 | 2056 (**exactly identical**) |
> | 2024-25 | 2149 | 2149 (**exactly identical**) |
> | 2025-26 | 2058 | 2058 (**exactly identical**) |
>
> A perfect null across all three seasons — not a regression (unlike attempts 4-6), but no improvement either. This is the cleanest possible confirmation of the theory from attempts 5-6: because red cards are *near*-certain, the pre-emptive filter and the reactive auto-sub/vice-captain fallback reach the *same* conclusion almost every time, so there's no downside (nothing gets wrongly guessed) but also no upside (auto-subs already caught these cases for free). Reverted — not because it hurt, but because it's provably dead code with zero behavioral effect, not worth the added complexity.
>
> **Open thread for a future attempt:** this only checked the immediately preceding gameweek, but serious-offense red cards carry 3-match bans, not 1 — a player suspended for games 2-3 of a longer ban wouldn't be caught by this check, and *that* case might not be covered by auto-subs either if the bot doesn't realize the ban is still active. Worth trying a 2-3 gameweek lookback specifically for that scenario.
>
> **The throughline across attempts 4-7:** every attempt to use injury/suspension data for *starting-XI or captaincy* selection on an already-fixed squad has landed somewhere between neutral and negative, because the simulation's existing auto-sub/vice-captain-fallback logic already captures most of the achievable value there for free. The unexplored, more promising application remains **transfer/wildcard decisions** — informing which players to own in the first place, where no equivalent free correction exists.

---

> [!WARNING]
> **An eighth attempt finally moved the data to that unexplored application — transfer/wildcard value, not XI/captaincy — and it was still a net regression, this time consistently across all three seasons rather than mixed.** A player's *current* gameweek's real chance-of-playing scaled their contribution to their horizon score (the multi-gameweek value used only for initial-squad/wildcard/transfer decisions) as a **soft, proportional expected-value discount** (a 60%-fit player contributes 60% of their predicted points), not a hard yes/no cutoff like attempts 5-6 — a genuinely different mechanism, deliberately kept away from XI/captaincy this time. Future weeks in the horizon (h ≥ 1) were left undiscounted, since a real manager doesn't know next month's fitness news any more than the bot does.
>
> | Season | Baseline (validated) | + transfer/wildcard availability discount |
> | --- | --- | --- |
> | 2023-24 | 2056 | 2050 (**-6**) |
> | 2024-25 | 2149 | 2058 (**-91**) |
> | 2025-26 | 2058 | 2025 (**-33**) |
>
> Worse in **all three** seasons (total 6263 → 6133, down 130) — the most consistently negative result of any attempt so far, even though no single season's drop is as large as some earlier ones. Reverted; not on `main`.
>
> **Working hypothesis, not fully verified (time-boxed, unlike some earlier root-causes in this plan):** discounting only the *current* week while leaving the next 4 gameweeks undiscounted creates an inconsistency — a player with a minor, temporary knock (say 80% this week, back to 100% next week) gets just enough of a horizon-score dip to occasionally tip a marginal transfer-or-hold decision toward selling, but not enough to reflect that they'll likely be fine again in a week. Since this re-evaluates fresh every gameweek, a transient dip could plausibly cause the bot to sell low and want to buy back a gameweek later — spending a real transfer (or a `-4` hit) reacting to noise that would have resolved itself for free. This wasn't directly measured (e.g. counting extra transfers/hits attributable to availability swings) before reverting, so treat it as the leading hypothesis, not a confirmed cause.
>
> **Where this leaves the whole line of experiments (4-8):** every application of real injury/suspension data tried so far — hard XI filters at two thresholds, a near-certain suspension filter, a model feature, and now a soft transfer-value discount — has landed neutral-to-negative. That's a real, useful finding in itself: this project's existing mechanisms (auto-subs, vice-captain fallback, and the model's own rolling-minutes features already discounting out-of-form players) apparently capture most of the achievable value from "knowing about injuries" already, at least for the specific mechanisms tried. A genuinely new angle — not yet tried — would be needed to beat that: e.g. weighting the discount by *time until the next gameweek* (a knock reported 3 days before deadline is more informative than one reported 3 weeks out), or only discounting when a transfer is already otherwise attractive rather than always applying it.

---

> [!IMPORTANT]
> **A community contributor (5H41L3N) opened a stacked series of PRs building on the state above.** Reviewed and merged: a live pipeline against the real FPL API (`model/live_pipeline.py`, PR #4 — reuses `train_model.build_season_features`/`optimizer`/`build_horizon_scores` unchanged rather than reimplementing them for live; the `.env` credential file it documented wasn't actually being loaded, fixed separately with `python-dotenv`); an investigation into whether the model's predictions were suspiciously compressed (PR #5) that disproved its own hypothesis across three separate tests and shipped no code change — kept as documentation so nobody re-derives it; a real data bug found and fixed (PR #10) where 2025-26's source `merged_gw.csv` shipped 10 byte-identical duplicate rows for one player, inventing a phantom double gameweek at GW2 that never happened (real 2025-26 doubles land at GW26/33/36) — `load_season()` now de-duplicates on `(element, GW, fixture)` before collapsing, and a genuine `fixture_count` feature was added (5th in feature importance, MAE 0.991 → 0.972); a fix (PR #11) making the horizon lookahead see *future* fixture counts, not just the current week's — without it, an upcoming double four gameweeks out was invisible to transfer planning, exactly blocking chip pre-positioning; and transfer-planning changes (PR #12) adding the AFCON GW16 free-transfer top-up documented in `FantasyRules.md` (2025/26 only, matching the same season-scoping discipline as the chip-count bug elsewhere in this plan), tightening the per-gameweek hit cap from 2 to 1 (2025-26 alone lost 108 points to hits under the old cap), and a price-movement model (`model/price_model.py`) wired in strictly as a tie-break between near-equal squads — sized against the measured 0.03-0.04 median gap between near-tied squads so it can only settle real ties, never override an actual points difference.
>
> Each of PRs #10-#12 came with its own measured before/after table in the PR description, but none of them actually landed in `plan.md` — worth recording here since this project treats `plan.md`, not GitHub PR descriptions, as the definitive experiment log. The cumulative, independently-verified result after merging #4/#5/#10/#11/#12 (before the availability-feature attempt below):
>
> | Season | Prior baseline | After #10-#12 | Diff |
> | --- | --- | --- | --- |
> | 2023-24 | 2098 | 2083 | -15 |
> | 2024-25 | 2016 | **2193** | **+177** |
> | 2025-26 | 1991 | **2049** | **+58** |
>
> Better in 2 of 3 seasons, a real aggregate gain (+220), consistent with this project's own aggregate-plus-most-seasons standard. The PR series' own component-by-component numbers (PR #10 alone lost 2023-24 to the de-dup fix removing inflated phantom-double points, recovered by PR #11's fixture-count-aware horizon; PR #12 then improved all three) are recorded in the PR history on GitHub, not reproduced here in full — the table above is the state actually verified against the merged code.

---

> [!WARNING]
> **A tenth attempt ran the one combination this plan had explicitly listed as never tried — availability as an isolated model feature, no filter, no change to how form is computed. It produced the best single-gameweek accuracy ever measured here and still lost on the season score.**
>
> The lead from the external review below was to "re-test attempt 4's model feature in complete isolation (no hard filter at all this time — the one combination never tried)". Attempt 9 had also shown *why* the previous attempt failed: rewriting form to be appearance-based removed the sell-pressure that decaying form quietly provides. So this attempt deliberately left form alone and only added a signal beside it, letting the model separate "low form because injured, now fit again" from "low form because playing badly".
>
> `chance_of_playing` joins from the fplcache archive on `(name, GW)` — **99.8-99.9% match rate for players who actually appeared** (the name instability documented elsewhere in this plan is a *cross-season* problem; within a season names are stable). FPL leaves the percentage null for players it has no concerns about (58.8% of `status = 'a'` rows, always 100 where present), and every flagged status carries an explicit number, so the encoding is lossless.
>
> **The mechanism works exactly as intended,** verified before measuring. Salah's AFCON block reads `chance_of_playing = 0` for GW17-22 and snaps back to 100 at GW23 the week he returns, while his rolling form still decays through the absence — so the sell-pressure attempt 9 destroyed is fully preserved. The feature is strongly monotonic against outcome: mean points by availability band run **0.004 / 0.033 / 0.585 / 1.343 / 1.721**, and the `chance = 0` band is 30% of all rows and scores essentially never.
>
> **Accuracy improved more than any change in this project's history: MAE 0.972 → 0.929, correlation 0.578 → 0.603.**
>
> | Season | Baseline | + availability feature | + also assuming recovery in the horizon |
> | --- | --- | --- | --- |
> | 2023-24 | 2083 | 2103 (+20) | 2086 (+3) |
> | 2024-25 | **2193** | 2146 (-47) | 2110 (-83) |
> | 2025-26 | **2049** | 2022 (-27) | 2049 (0) |
> | **aggregate** | **6325** | **6271 (-54)** | **6245 (-80)** |
>
> **The second column tested a specific hypothesis and falsified it.** `build_horizon_scores()` freezes every feature across the five-week lookahead, so an injured player was valued at zero for five consecutive weeks — sold, then bought back once fit, burning transfers on news that resolves itself. That is exactly the churn attempt 8 hypothesised but never verified. Assuming recovery in future weeks (a real manager knows this week's team news and nothing beyond it) made the result **worse, not better**. Churn is not the mechanism. Reverted.
>
> **What ten attempts now establish.** This is no longer "we haven't found the right mechanism yet". Availability data has been applied as a hard XI filter at two thresholds, a near-certain suspension filter, a bundled model feature, a soft transfer-value discount, a rewrite of form itself, an isolated model feature, and that feature with a corrected horizon. Every single one landed neutral-to-negative. The isolated feature is the cleanest test of all — it is strictly more information, correctly encoded, verified to behave as designed, and it improves prediction accuracy by the largest margin ever recorded here.
>
> **The sharpest finding is the decoupling itself: a 4.4% MAE improvement produced a 0.9% season-score regression.** Single-gameweek accuracy and season score are not merely weakly related in this architecture — on this evidence they can point in opposite directions. Any future change justified by MAE alone should be treated as unsupported until it is scored, and this plan already contains two other examples (attempt 4's MAE 0.991 → 0.942, and the xG feature set) pointing the same way.
>
> One honest caveat against reverting: the availability variant has a *tighter and higher floor* (+100/+138/+127 against the average manager, spread 38) than the baseline it loses to (+80/+185/+154, spread 105). For a single live season — which is one draw, not an average — that is a real argument. It was reverted anyway, because keeping a change that loses 54 points aggregate and wins in 1 of 3 seasons would be applying a standard this project has not applied elsewhere.

---

> [!IMPORTANT]
> **The full contributor stack (#4, #5, #10, #11, #12, #13) is now merged and independently re-verified against the actual merged code**, not just the individual PR descriptions:
>
> | Season | Avg manager | Previous strongest | Merged stack |
> | --- | --- | --- | --- |
> | 2023-24 | 2003 | 2098 (+95) | **2055 (+52)** |
> | 2024-25 | 2008 | 2016 (+8) | **2193 (+185)** |
> | 2025-26 | 1895 | 1991 (+96) | **2049 (+154)** |
> | **aggregate** | 5906 | 6105 | **6297 (+192)** |
>
> Verified reproducible (reran 2023-24 independently and got the same 2055). Worth noting the 2023-24 figure doesn't match PR #12's own reported 2083 for the equivalent combined state — 2024-25 and 2025-26 both match PR #12's numbers exactly, so this looks like a stale figure in that PR's description (likely measured before a later commit in the same branch) rather than a merge problem, but it's flagged here rather than silently accepted, consistent with this project's rule of verifying claimed numbers rather than trusting them.
>
> **A seventh PR in the stack (#14, "fire chips on data instead of the calendar") replaces the fixed Wildcard/Bench Boost calendar weeks and the forwards-only Triple Captain trigger with data-driven equivalents.** Its own PR description is admirably honest about the single-seed result: better in only 1 of 3 seasons (2023-24 +174), carried by one large gain, with two seasons going backwards, kept only on aggregate plus a structural argument, and three unswept trigger parameters (`WILDCARD_TRIGGER_MARGIN`, `BENCH_BOOST_TRIGGER`, `MIN_CHIP_GW`). This was exactly the shape of change `TRANSFER_MARGIN` turned out to be two entries above — initially left open pending the same 5-seed sweep this project already learned it needs before trusting a change like this.
>
> **Merged, swept, and reverted.** Ran the same 5-seed × 3-season methodology on the shipped defaults:
>
> | Season | Pre-#14 baseline | Sweep median | Diff |
> | --- | --- | --- | --- |
> | 2023-24 | 2055 | 2125 (range 2076-2218) | **+70** |
> | 2024-25 | 2193 | 2101 (identical across all 5 seeds) | **-92** |
> | 2025-26 | 2049 | 2004 (identical across all 5 seeds) | **-45** |
> | **aggregate** | **6297** | **6230** | **-67** |
>
> Worse in 2 of 3 seasons *and* worse in aggregate — fails both standards this project uses. The 2024-25/2025-26 losses aren't noise: all 5 seeds gave byte-identical totals for each of those two seasons, meaning the new triggers deterministically land on a worse outcome there regardless of model variation, not an unlucky draw. Only 2023-24 improved, by less than the other two seasons lost combined. Reverted via a clean forward commit (`git revert`, not a history rewrite) — the merge commit stays in the log, this is exactly the discipline the PR's own author asked for, not a rejection of the idea. Worth revisiting if the three trigger parameters are retuned and re-swept, since 2023-24's real, seed-robust gain (range 2076-2218, all above the 2055 baseline) suggests the *mechanism* has genuine value even though the specific unswept defaults didn't survive.
>
> Two smaller gaps found during review, not yet fixed: `live_pipeline.py` never plays any chip at all (Wildcard/Bench Boost/Triple Captain/Free Hit, and PR #12's price-tiebreak/AFCON-topup/hit-cap alignment, are backtest-only — the live path has its own separate, simpler transfer-only decision loop that doesn't call into any of `simulate_season.py`'s chip logic), and `blank_gameweek_rows()` (PR #10) uses a player's *last* row as the template for their team when backfilling blanks, which could misattribute team for gameweeks before a mid-season transfer.

---

> [!IMPORTANT]
> **External review of attempts 1-8** (a "hard thinking" pass by another model, given the full context in [`HardThinkingPrompt.md`](HardThinkingPrompt.md)) surfaced two falsifiable claims and several concrete leads. Both claims were checked against the actual code/data before acting on either — one held up, one didn't:
>
> - **Claimed: auto-sub "leakage"** — that `apply_auto_subs` picks whichever bench player scored *most* in hindsight, giving the backtest an unrealistic advantage real managers don't have. **Checked against the code and this is not what it does**: the substitution loop breaks on the *first* eligible bench player while iterating in a fixed priority order (bench is ranked by `predicted_points` in `optimizer.pick_starting_xi`, decided *before* results are known), not the best-in-hindsight one. This already matches FPL's real first-eligible-in-order substitution rule. Not changed.
> - **Claimed: the simulator gives every backtested season the same chip allowance as 2025/26.** **Checked against the official rules and this was correct** — see the correction above. Fixed.
>
> Concrete leads accepted and being worked through (in priority order): re-test attempt 4's model feature in complete isolation (no hard filter at all this time — the one combination never tried); investigate attempt 8's "sell-then-rebuy churn" hypothesis directly rather than leaving it unverified; check whether a price-timing angle is worth building (**precondition checked first, as suggested**: does a real availability flag actually predict a price drop in the next 2 gameweeks? Diluted to near-zero across the whole player pool, since most players are barely owned and can't move price regardless — but restricted to the top 10% most-owned players, flagged players lost **-0.27** in value over the next 2 gameweeks vs. **+0.05** for non-flagged ones. Real, meaningful effect for popular players specifically — worth pursuing); and building a placebo/noise-floor test (perturb something functionally meaningless — e.g. the model's random seed — and see how much the season score moves on its own, to judge whether the ±50-150 point swings seen across attempts 1-8 are distinguishable from noise at all, given only 3 validation seasons).
>
> The review's sharpest methodological point: **"better-or-equal across most of 3 seasons" passes about 50% of the time by pure chance under a true null effect** — this project's own validation bar has never been checked against a noise floor. The placebo test above is designed to establish exactly that before trusting any future attempt's multi-season result at face value.

---

> [!IMPORTANT]
> **The placebo/noise-floor test.** Changed nothing except the model's random seed (42 → 43 — a functionally meaningless perturbation, same features, same data, same architecture) and reran the full multi-season backtest:
>
> | Season | Corrected baseline (seed 42) | Placebo (seed 43) | Pure noise |
> | --- | --- | --- | --- |
> | 2023-24 | 2040 | 2054 | +14 |
> | 2024-25 | 2118 | 2084 | -34 |
> | 2025-26 | 2058 | 2030 | -28 |
>
> So the genuine noise floor for a season-total score, given nothing meaningfully changed, is roughly **±15 to ±35 points**. Reverted immediately after measuring — seed 43 was never meant for `main`.
>
> **Recalibrating every prior result against this floor** (using the pre-chip-fix baseline numbers each experiment was actually measured against, since that's what's comparable):
>
> - **Attempt 7** (0, 0, 0): a real, exact null — trivially below the noise floor, as expected (it's a provable no-op, not a measurement).
> - **Attempts 5 and 6** (-15/-76/0 and -12/-70/0): the 2024-25 swing (-70 to -76) is roughly **2-5x the largest noise swing observed** — very likely a real effect, not noise. The 2023-24 figures (-12 to -15) and the 2025-26 zeros sit at or within the noise band and shouldn't be over-interpreted on their own — but they don't need to be, since 2024-25 alone is enough to trust the "this regressed" conclusion.
> - **Attempt 8** (-6/-91/-33): the 2024-25 figure (-91) is far beyond the noise floor. The 2025-26 figure (-33) sits right at the edge of the noise band — plausibly a mix of real effect and noise. The 2023-24 figure (-6) is well within noise and shouldn't be treated as evidence either way.
> - **Attempt 4** (-80/+81/-34, pre-chip-fix baseline): both the -80 and +81 are well beyond the noise floor, in *opposite* directions — a genuinely mixed, real effect, not noise. The 2025-26 figure (-34) sits at the noise floor's edge.
>
> **Bottom line: the noise floor is real and non-trivial (~15-35 points), but most of the larger swings across attempts 4-8 — especially the 2024-25 results — are still comfortably distinguishable from it.** The core conclusions (regressions in 4, 5, 6, 8; a genuine null in 7) survive this check. What the noise floor *does* invalidate is treating any single season's ±10-35 point figure as meaningful on its own — several individual per-season numbers in this plan (e.g. attempt 8's 2023-24 result) should be read as "not distinguishable from noise," not as evidence of a small real effect. Future attempts should be judged the same way: a swing has to clear roughly ±35 points in a season to be trusted as more than noise, not just be non-zero.

---

> [!TIP]
> [sertalpbilal/FPL-Optimization-Tools](https://github.com/sertalpbilal/FPL-Optimization-Tools) (HiGHS solver via `sasoptpy`) remains a good reference for going further — e.g. true rolling-horizon lookahead (planning transfers *ahead* of the gameweek they're needed) rather than this project's greedy week-by-week approach.

---

> [!IMPORTANT]
> **A follow-up review of the whole project (not just injury data) turned up two more real bugs, both fixed:**
>
> - **Cold-start bug.** Players with no rolling-form history yet (a promoted club's players, a fresh transfer's first gameweek) had every rolling-stat feature filled with `0` — an input the model was never actually trained on, since `train_model.prepare()` drops exactly these no-history rows during training (`dropna` on the first rolling column). A `0` reads to the model as "this player never plays," not "unknown." Fixed to fall back to the **position's average** for that stat instead — a more reasonable "unknown, treat as an average player of this position" prior (`simulate_season.build_predictions`). Verified the fix actually engages (362 affected rows in 2025-26 alone) but produced a **byte-identical multi-season backtest score** (2040/2118/2058), which is consistent with either "the fix has no opportunity to matter this season" or "the fix isn't actually reaching the optimizer" — checked directly rather than assuming: sampled true cold-start players at 2025-26 GW1 (raw-NaN rolling history, not a real average that happens to equal zero) and compared `predicted_points` before/after. All moved clearly off near-zero (e.g. Thierno Barry 0.25→1.73, Josh Laurent 0.09→1.48, Tom Watson 0.11→1.50) — the fix reaches the optimizer correctly, it just never happened to flip an actual squad/transfer decision in these three seasons, since the affected players are mostly £40-60m fringe/promoted-squad picks that weren't competitive even at the corrected prediction. Genuine null, confirmed rather than assumed. Kept anyway, since this is a correctness fix for *live* use, not a speculative feature — 2026-27 will have real promoted-club first-teamers (Leeds, Burnley, Sunderland) this backtest can't stress-test, and going into the season predicting near-zero points for them regardless of ability was a real gap.
> - **Sell-price rule bug.** `squad_value()` used every owned player's full *current live market value* as the funds available for transfers/wildcard. Real FPL only refunds a price **rise** since purchase at half profit (rounded down) — a price fall is passed on in full, but a rise is not fully realized until you actually cash it in gradually. The old code effectively let the bot treat price gains as instantly, fully liquid, which no real manager gets. Fixed by tracking each squad member's purchase price (`carry_purchase_prices`) and pricing transfers/wildcard candidates by real sell value (`sell_value`, `squad_sell_value`, `with_sell_cost` in `simulate_season.py`; `optimizer.select_squad` gained a `cost_col` param so retained players can be priced differently from new buys in the same budget constraint).
>
>   | Season | Before | After | Diff |
>   | --- | --- | --- | --- |
>   | 2023-24 | 2040 | 2093 | **+53** |
>   | 2024-25 | 2118 | 2042 | **-76** |
>   | 2025-26 | 2058 | 1987 | **-71** |
>
>   Mixed direction, and two of the three swings are well beyond the ~±35 noise floor — on its face this looks like a regression by this project's own "better-or-equal in most of 3 seasons" bar. **Kept anyway, for the same reason as the chip-count bug fix (above): this isn't a speculative feature that either helps or doesn't, it's a correction to a budget rule that was previously wrong in a specific, structural direction** (overstating available funds), so the score moving — in either direction, since a tighter budget can force a *different* candidate squad at the same transfer count, not just fewer transfers — is expected, not evidence the fix is bad. The chip-count fix set this precedent already: it also lowered two of three season scores and was kept as "the corrected figures." These are the new corrected figures.
>
> **Isolation check, since cold-start + sell-price were originally reported as a bundled swing (the same mistake as attempts 1 and 3 if left unverified):** re-ran each fix alone against the chip-fix-only baseline (2040/2118/2058).
>
> | Season | Baseline | Cold-start alone | Sell-price alone | Both combined |
> | --- | --- | --- | --- | --- |
> | 2023-24 | 2040 | 2040 (+0) | 2093 (+53) | 2093 (+53) |
> | 2024-25 | 2118 | 2118 (+0) | 2042 (-76) | 2042 (-76) |
> | 2025-26 | 2058 | 2058 (+0) | 1987 (-71) | 1987 (-71) |
>
> Clean result: sell-price-alone matches the combined number exactly in all three seasons. Cold-start contributes exactly zero both alone and stacked with sell-price — no interaction effect, and the entire swing is attributable to the sell-price fix by itself, not a second bug hiding in the bundle.
>
> Multi-season results as currently validated (both fixes applied): **2023-24 → 2093 (+90 vs avg)**, **2024-25 → 2042 (+34 vs avg)**, **2025-26 → 1987 (+92 vs avg)**. Still beats the average manager in every season, by a real but smaller and more honest margin than before either fix.
>
> **The +53/-76/-71 swing above was initially explained away as "the same phenomenon as the established ±15-35 noise floor" — that was an unchecked assertion, not a measurement, and it turned out to be wrong.** Ran 2 more placebo seeds (43, 44) on the corrected pipeline specifically, rather than assuming the old noise-floor measurement (taken before any of these fixes existed) still applied:
>
> | Season | Sell-price-fix baseline (seed 42) | Seed 43 | Seed 44 |
> | --- | --- | --- | --- |
> | 2023-24 | 2093 | 1973 (**-120**) | 1968 (**-125**) |
> | 2024-25 | 2042 | 2020 (**-22**) | 1995 (**-47**) |
> | 2025-26 | 1987 | 2065 (**+78**) | 1978 (**-9**) |
>
> This pipeline's own noise floor is **far larger** than the ±15-35 measured pre-fix — up to ±125 in 2023-24 alone. Isolated the cause by re-running the same seed swap on the sell-price fix alone (no cold-start, no name-matching, which are already known to contribute ~0): nearly identical amplified noise (-120/-22/+78), confirming the sell-price fix itself, not the other two fixes, is the source.
>
> Consequence: the originally reported **+53 in 2023-24 is not distinguishable from this pipeline's own noise** (noise alone reaches -120 to -125 in that season). The **-76 in 2024-25** is more likely a real effect (76 > the 22-47 noise observed there, though only 2 extra samples). The **-71 in 2025-26** is ambiguous (noise ranged from -9 to +78 there). **The per-season point totals should not be read as precise measurements of the sell-price fix's effect size** — only 2023-24's headline "+53" claim is now actively retracted as unsupported; the fix itself stays (it corrects a genuinely wrong budget rule, and that correctness doesn't depend on knowing the exact score impact), but the specific magnitude claims made about it above should be read with this much wider uncertainty band, not at face value.
>
> **Likely mechanism, not yet confirmed:** the sell-price fix introduces path dependency that the old flat full-market-value budget never had — tracking purchase price per player means an early transfer decision permanently changes future gameweeks' available budget, so a small prediction difference early in the season (e.g. from a seed swap) can compound and diverge across 38 gameweeks of transfer decisions, rather than being reset each week the way the old budget calculation implicitly was. Plausible, not yet verified against the actual squad/transfer divergence between seeds.
>
> **Supporting context gathered before the noise floor was re-measured** (now read with the caveat above in mind): plotted the cumulative point differential (sell-price-fix minus baseline) week by week for 2023-24, seed 42 only. Mostly steady, small per-week gains (0-20 points), no single gameweek dominating the total, accelerating in the back third of the season (roughly GW23-38, where the cumulative gap grew from +29 to +53) — consistent with a genuine, compounding, season-long effect rather than one lucky transfer window, *for this one seed's trajectory specifically*. Given the noise floor just found, this is one sample from what's now known to be a much wider distribution of possible trajectories, not proof the effect is real — just evidence that, when it manifests, it doesn't hinge on a single decision point.
>
> **Extended to 8 seeds (43-50) across all 3 seasons, confirming this is worse than a wide floor — the distribution is not smooth, it's clustered/bimodal in the worst season:**
>
> | Season | n | min | max | range | mean | stdev | sorted values |
> | --- | --- | --- | --- | --- | --- | --- | --- |
> | 2023-24 | 9 (incl. seed 42) | 1960 | 2172 | 212 | 2020.1 | 81.7 | 1960, 1968, 1968, 1968, 1968, 1973, **2093**, 2111, 2172 |
> | 2024-25 | 9 | 1972 | 2042 | 70 | 2014.2 | 25.9 | 1972, 1995, 1995, 1995, 2020, 2025, 2042, **2042**, 2042 |
> | 2025-26 | 9 | 1978 | 2063 | 85 | 1989.8 | 27.6 | **1978**, 1978, 1978, 1978, 1981, 1981, 1984, 1987, 2063 |
>
> (Corrected after an initial version of this table wrongly bolded 2063 — seed 43's value — instead of seed 42's actual 1978 for 2025-26, which is tied for the *most common* value in that season, not an outlier. Verified by rerunning seed 42 directly: 1978, confirmed, matches the officially validated number. Not a determinism bug — a table-formatting mistake, caught before being trusted further.)
>
> 2023-24 is the striking one: **6 of 9 seeds land in a tight cluster around 1968**, while 3 (including the officially-reported seed 42, at 2093) land in a distinctly higher cluster around 2093-2172. That's not a smooth ±spread around a true mean — it looks like two qualitatively different outcomes ("basins"), and which one a given seed lands in depends on an essentially arbitrary tie-break. **The seed this project has used as its headline number all along (42) is the minority/atypical outcome for 2023-24** specifically — 2024-25 (2042) and 2025-26 (1978) are both squarely modal, not outliers. Going forward, a median/mean across several seeds is a more honest headline number than a single arbitrary seed, until the underlying fragility (below) is addressed.
>
> **Root cause traced to source, confirmed rather than inferred.** Compared full prediction arrays for seed-42 vs seed-43 models on the actual 2023-24 `build_predictions()` output (28,742 rows): only **17 rows differ at all**, max difference **0.74 points** (`GradientBoostingRegressor` with `subsample=1.0`, no `max_features` restriction has almost no genuine random component — `random_state` only affects rare internal tie-breaks). The very first and largest of those 17 differences is **Kieran Trippier's GW9 prediction, off by 0.74** — landing right at GW8, the Wildcard gameweek. Tracked squad composition week-by-week for seed 42 vs 43: **identical for GW1-7, first diverge at GW8 (the Wildcard rebuild)**, then the gap widens steadily for the rest of the season (squad-name differences climbing from 3 players at GW8 to 8-9 by GW34+, cumulative score gap growing from -3 to over -120) — a genuine compounding path, not a single unlucky week. Ran the identical seed42-vs-43 divergence check on the **old, no-sell-price-fix budget model**: **zero divergence at any gameweek, all 38 weeks identical** — confirming the sell-price fix's purchase-price path dependency is precisely what turns a 0.74-point, single-player tie-break into a 100+ point season-level swing. Without it, the same tiny prediction difference at the same Wildcard week apparently wasn't enough to flip the decision at all.
>
> **This is a genuine live-deployment risk, not just a backtest-measurement problem.** A production run gets exactly one draw — if essentially arbitrary, floating-point-level prediction noise can flip a Wildcard-week transfer decision that then compounds for the rest of the season, the bot's real-world performance is far less predictable than the pre-sell-price-fix pipeline was. A natural mitigation (not yet built, not yet validated): a stability threshold on transfer decisions — only take a transfer if its lookahead gain clears a minimum margin over holding, not just "marginally better," so razor-thin, untrustworthy margins don't get acted on. Worth investigating once there's confidence in what's driving the fragility (now confirmed: the sell-price fix's budget path-dependency), since building a fix for a described-but-unconfirmed fragility would have been premature.
>
> **Before picking a threshold: characterized how common near-zero-gap decisions actually are.** Extracted the gap between the chosen option and its next-best alternative at every squad-construction decision (GW1, both Wildcards, and every ordinary transfer week) across all 3 seasons — for GW1/Wildcard, by re-solving the ILP with the chosen 15-player squad excluded (forcing ≥1 player to differ) to find the true next-best squad; for transfer weeks, from the existing k=0..5 candidate search already computed.
>
> | Decision kind | n | mean gap | median gap | max gap | # under 1.0pt | # under 0.74pt (the Trippier-sized diff) |
> | --- | --- | --- | --- | --- | --- | --- |
> | GW1 | 3 | 0.14 | 0.04 | 0.36 | 3/3 | 3/3 |
> | Wildcard | 6 | 0.08 | 0.03 | 0.20 | 6/6 | 6/6 |
> | Transfer | 104 | 1.22 | 0.91 | 11.31 | 59/104 (57%) | 46/104 (44%) |
>
> **Every single GW1 and Wildcard decision across all three seasons — 9 of 9 — sits under a full point from its next-best alternative, with a median gap around 0.03-0.04.** This isn't occasional fragility; it's the norm for full squad-rebuild decisions. A 0.74-point prediction noise level (the actual size of the Trippier discrepancy above) isn't a rare unlucky draw clearing some threshold — it's comfortably larger than the *typical* GW1/Wildcard gap, meaning essentially every Wildcard decision this project has ever simulated was decided by a margin smaller than ordinary model noise. Ordinary transfer weeks are meaningfully different: a majority (57%) are also under 1 point, but there's a real tail out to 11+ points where the decision is genuinely clear-cut — transfers are not uniformly fragile the way full rebuilds are.
>
> **Consequence for the stability-threshold idea:** a single fixed margin sized to filter out sub-1-point gaps would barely touch ordinary transfer weeks (46% still clear it) but would functionally neuter GW1/Wildcard decision-making, since the "winning" option is essentially always statistically tied with several others there — there's rarely a genuinely dominant squad choice at a full rebuild, just a huge number of near-equivalent 15-player combinations differing by fractions of a point. A margin large enough to matter at Wildcard weeks specifically would need its own, larger threshold than routine transfer weeks — exactly the asymmetry flagged as worth checking before picking one number. This also reframes the problem slightly: the danger isn't that Wildcard decisions are *unusually* fragile compared to some normal baseline, it's that the combinatorial optimization landscape for a full 15-player rebuild inherently produces many near-tied optima, and the sell-price fix's path dependency is what turns "arbitrarily picking among near-ties" from a harmless quirk into a season-defining, hard-to-reverse commitment.
>
> **A margin doesn't fit GW1/Wildcard structurally — there's no "hold" alternative at a full rebuild, and Wildcard currently fires unconditionally on the calendar rather than as a data-driven choice.** Built the alternative instead: average predictions from a 5-model ensemble (canonical model + 4 more, seeds 101-104, same features, independently trained) specifically for GW1 and Wildcard squad construction only — `ensemble_predict()`/`ENSEMBLE_EXTRA_SEEDS` in `simulate_season.py`. Ordinary transfer weeks keep using the single canonical model, since their gap distribution (above) is meaningfully different, not uniformly razor-thin.
>
> **Validated before trusting it, not after:** reran the same canonical-seed-42-through-50 sweep on 2023-24 with the ensemble fix in place, to see whether it actually collapsed the 1968/2093 split.
>
> | Canonical seed | Before ensemble fix | After ensemble fix |
> | --- | --- | --- |
> | 42 | 2093 | **1973** (moved) |
> | 43 | 1973 | 1973 |
> | 44 | 1968 | 1968 |
> | 45 | 2172 | **2172** (unchanged) |
> | 46 | 1960 | 1960 |
> | 47 | 1968 | 1968 |
> | 48 | 1968 | 1968 |
> | 49 | 2111 | **2111** (unchanged) |
> | 50 | 1968 | 1968 |
>
> **Honest result: partial, not a fix.** Only seed 42 moved out of the high cluster; seeds 45 and 49 landed at *exactly* their pre-fix values, meaning the extra 4 models in the ensemble didn't change those specific GW1/Wildcard decisions at all. The range is unchanged (1960-2172, still 212 points) and the split is still bimodal — the fix reduced the high cluster from 3 of 9 to 2 of 9 seeds, not to zero. Kept as a genuine partial improvement (one fewer seed lands in the unstable-fork territory, at a real computational cost of training 5x the models), but this doesn't resolve the underlying fragility — some GW1/Wildcard decisions are apparently decided by a difference robust enough to survive 5-model averaging, not just single-model noise. Not escalating the ensemble size further without more evidence that a bigger ensemble would help rather than just being more expensive for the same partial result.
>
> **Traced the seed 45/49 residual divergence directly rather than assuming it's the same story.** First confirmed the ensemble is actually wired to *both* Wildcard gameweeks (`elif gw in WILDCARD_GWS:` is a single branch covering GW8 and GW20 — not a partial-application bug). Then diffed full prediction arrays for canonical seed 45 vs. a fixed (post-ensemble) seed 44: same tiny-magnitude pattern as the original Trippier case — 17 of 28,742 rows differ, max difference 0.95 points, again touching Trippier/Doughty specifically. Walked the squad week by week under the real ensemble-enabled pipeline: **identical through GW7, first diverge exactly at GW8 (Wildcard) with 3 players differing**, then the gap compounds for the rest of the season (-3 at GW8 to +204 by GW38) — the *same* decision point as the original fork, not a new or different mechanism. For this specific seed's tiny prediction gap, averaging in 4 more models simply didn't land on the other side of the tie, unlike seed 42's case.
>
> **Consequence, checked before assuming: `TRANSFER_MARGIN` cannot resolve this fork, structurally.** The margin logic only gates ordinary transfer-week decisions (`else:` branch in `simulate()`) — it is never applied at GW1 or Wildcard, by design (a margin needs a "hold" alternative to compare against, which doesn't exist at a full rebuild — see above). Since this divergence is a Wildcard-week decision, no value of `TRANSFER_MARGIN` can touch it. Whatever the margin sweep below shows, it is answering a different, real question (does it help ordinary-week fragility) — not this one.
>
> **Multi-seed validated the margin sweep, not a single canonical run per margin** (5 seeds — 42, 43, 44 known post-ensemble-fix "low cluster", plus 45, 49 the two residual "stuck" outliers — × margins 0.0/1.0/2.0 × all 3 seasons, 45 runs):
>
> | Season | Margin | min | max | range | median |
> | --- | --- | --- | --- | --- | --- |
> | 2023-24 | 0.0 | 1968 | 2172 | 204 | 1973 |
> | 2023-24 | 1.0 | 2087 | 2093 | **6** | **2093** |
> | 2023-24 | 2.0 | 2113 | 2118 | 5 | 2118 |
> | 2024-25 | 0.0 | 1995 | 2042 | 47 | 1995 |
> | 2024-25 | 1.0 | 1995 | 2042 | 47 | 1995 |
> | 2024-25 | 2.0 | 1950 | 1963 | 13 | **1960 (regression)** |
> | 2025-26 | 0.0 | 1978 | 2063 | 85 | 1981 |
> | 2025-26 | 1.0 | 1978 | 2063 | 85 | 1981 |
> | 2025-26 | 2.0 | 2004 | 2044 | 40 | 2042 |
>
> **Margin=1.0 is strictly better-or-equal to margin=0.0 in every season, on every metric** — 2023-24's spread collapses from 204 to 6 points and its median jumps from 1973 to 2093; 2024-25 and 2025-26 are **byte-identical** to the no-margin baseline (zero risk). Margin=2.0 pushes 2023-24 slightly further (spread 5, median 2118) and helps 2025-26 more (median 2042 vs 1981), but at a real cost in 2024-25: its spread also tightens, but its median *drops* 35 points, a genuine regression the same shape as the sell-price fix's original 2024-25 cost. **Chose `TRANSFER_MARGIN = 1.0`** — captures nearly all of 2023-24's benefit with none of margin=2.0's downside, the cleanest result of this entire investigation (strictly better-or-equal everywhere, not just "most" seasons).
>
> **This also corrects the "structurally cannot resolve this fork" claim above — the implication was wrong, even though the mechanism claim was right.** At margin=1.0, seed 45 (2172→2093) and seed 49 (2111→2093) converge almost exactly with the other three seeds in 2023-24. The GW8 fork itself still happens — margin genuinely cannot change what gets picked at a Wildcard, that part of the earlier claim holds — but margin stops the fork's *consequence* from snowballing: with fewer marginal, noisy transfers taken across the remaining 30 gameweeks, two squads that started from different GW8 picks stop diverging further and end up in similar territory anyway. The fork isn't prevented, but it stops compounding — which was always the actual mechanism turning a 0.74-point tie-break into a 200-point season swing (see the sell-price path-dependency finding above). This is inferred from the convergence pattern in the score distribution, not re-confirmed with another explicit squad-by-squad trace — flagging that distinction rather than overstating confidence.
>
> **Multi-season headline updated, `TRANSFER_MARGIN = 1.0` now shipped:** canonical seed 42 — **2023-24: 1973 → 2087**, 2024-25: 2042 (unchanged), 2025-26: 1978 (unchanged). Strictly better-or-equal across all three seasons for the actual shipped model, not just the multi-seed aggregate.

---

> [!IMPORTANT]
> **Free Hit, isolated for the first time.** Every prior test of Free Hit was bundled with several other changes (the very first Phase-4 experiment, which regressed as a whole and was reverted — its Free Hit component's standalone value was never actually known). Implemented cleanly: unlimited free transfers for exactly one gameweek, squad reverts automatically the next week with no lasting budget/transfer-count cost (`is_free_hit` handling in `simulate()`). Triggered like Triple Captain — data-driven, not a fixed calendar week: play it when a single-gameweek-optimal unconstrained squad clearly beats the current squad's actual best XI *this week only* (`FREE_HIT_TRIGGER_MARGIN = 10.0`, this week's predictions, not horizon-valued — matches the project's existing rule that starting-XI-scale decisions stay single-gameweek), forced on the half's last gameweek if never triggered so it isn't wasted. Explicitly excludes `bench_boost_gws` — Bench Boost is a fixed calendar week with no re-trigger window, so letting Free Hit claim it would silently skip Bench Boost for the rest of the half rather than just deferring it.
>
> | Season | Before | After | Diff |
> | --- | --- | --- | --- |
> | 2023-24 | 2087 | 2110 | **+23** |
> | 2024-25 | 2042 | 2022 | **-20** |
> | 2025-26 | 1978 | 2025 | **+47** |
>
> Mixed, but checked against each season's already-known noise floor rather than taken at face value: 2023-24's score-distribution spread was collapsed to just 6 points by the margin fix above, so +23 there is comfortably beyond noise — likely real. 2024-25's spread was still ~47 points even post-margin-fix, so the -20 "regression" sits entirely inside that existing noise band — not a confirmed regression, just not distinguishable from it either way. 2025-26's spread was ~85 points, so +47 is suggestive of a real effect without being conclusive on its own. **Kept**, on a "2 of 3 improve, the third isn't a confirmed loss" basis consistent with this project's validation bar — not multi-seed-swept the way the margin fix was, given the noise floors for these specific seasons were already characterized in the sell-price/ensemble investigation above and a fresh 45-run sweep wasn't judged worth the time for a result this directionally clear.
>
> Multi-season headline now: **2023-24 → 2110 (+107 vs avg)**, **2024-25 → 2022 (+14 vs avg)**, **2025-26 → 2025 (+130 vs avg)**.

---

> [!WARNING]
> **The `TRANSFER_MARGIN = 1.0` decision above was reconsidered — a real reproducibility scare turned out to have a mundane cause, and re-checking properly then exposed an inconsistency in how the decision itself was made.**
>
> **The scare:** re-verifying the original margin sweep found the exact same config (seed 42, margin 1.5, 2024-25) giving 1968 in the original run but 2016 on every re-check — including two independent fresh-process runs that agreed with *each other* (ruling out solver/model non-determinism) and a same-process sequential-margin run (ruling out state contamination between margin values). The actual cause: **Free Hit was added to the codebase (commit `2ffbe4e`) *after* the original margin sweep ran (`922c924`)**, so every re-verification in this investigation was unknowingly testing margin's effect on a different pipeline — one where Free Hit competes for the same weekly decision slots — than the one that produced the original numbers. Confirmed directly: disabling Free Hit (`FREE_HIT_TRIGGER_MARGIN = 1e9`) reproduced 1963, matching the original 1968 almost exactly. Not a bug, not non-determinism — an experimental design oversight (re-testing an old result against a codebase that had since changed underneath it).
>
> **Since Free Hit is now permanent, the margin sweep needed a full, honest re-run on the current pipeline, not a reconciliation against stale numbers.** Re-ran the same 5-seed × 3-season methodology, now with margin 1.5 included alongside 0.0/1.0/2.0 (60 runs total):
>
> | Season | Margin | min | max | range | median |
> | --- | --- | --- | --- | --- | --- |
> | 2023-24 | 0.0 | 1986 | 2120 | 134 | 2025 |
> | 2023-24 | 1.0 | 2040 | 2153 | 113 | 2045 |
> | 2023-24 | 1.5 | 2098 | 2116 | 18 | 2098 |
> | 2023-24 | 2.0 | 2099 | 2116 | 17 | 2099 |
> | 2024-25 | 0.0 | 1986 | 2022 | 36 | 1986 |
> | 2024-25 | 1.0 | 1986 | 2022 | 36 | 1986 |
> | 2024-25 | 1.5 | 1981 | 2016 | 35 | **2016** |
> | 2024-25 | 2.0 | 1966 | 2007 | 41 | 1997 |
> | 2025-26 | 0.0 | 2012 | 2028 | 16 | 2025 |
> | 2025-26 | 1.0 | 2012 | 2028 | 16 | 2025 |
> | 2025-26 | 1.5 | 1932 | 1993 | 61 | **1991 (regression)** |
> | 2025-26 | 2.0 | 1928 | 1998 | 70 | 1996 |
>
> **Aggregate median sum across all 3 seasons: 0.0 → 6036, 1.0 → 6056, 1.5 → 6105 (highest), 2.0 → 6092.**
>
> **The inconsistency this surfaced:** margin=1.0 is better-or-equal to 0.0 in *all three* seasons (a strict, riskless improvement), but margin=1.5 has the highest aggregate score and is better in 2 of 3 seasons — its one loss (2025-26, -34) is real and confirmed, not noise (the score distributions at margin 1.0 and 1.5 for 2025-26 don't overlap at all: 1.0's range is 2012-2028, 1.5's is 1932-1993). The original write-up picked 1.0 anyway, using an unstated stricter standard ("reject anything with any confirmed regression, even outweighed elsewhere") that was never applied to the Free Hit decision made in the very same investigation — Free Hit was kept on a "2-of-3-improved, better aggregate" basis despite its own real 2024-25 dip. **Corrected: `TRANSFER_MARGIN` is now `1.5`, chosen by the same standard actually used elsewhere in this project** (aggregate performance across the 3-season backtest, better-or-equal in most, not zero-regression-anywhere). This is a judgment call about how much to weight one season's confirmed loss against two seasons' confirmed gains, not a purely empirical result — flagging it as a stated choice, per the standard this project has otherwise used, rather than a default that fell out of how the result happened to be framed.
>
> Multi-season headline updated again: canonical seed 42 — **2023-24: 2110 → 2098**, **2024-25: 2022 → 2016**, **2025-26: 2025 → 1991**.

---

> [!NOTE]
> **Precondition check: is a two-stage model (predict minutes, then points-per-90 conditional on playing) worth building?** The suggested cheap test: bucket the current model's residuals by real minutes played — if error is *dramatically worse specifically in the 1-45 minute band* than at 0 or 90, that points at a real mixed-distribution problem (a single model straining to fit both "did they play at all" and "how well did they play" at once) worth splitting into two stages. Result on the 2025-26 backtest:
>
> | Minutes band | MAE | n |
> | --- | --- | --- |
> | 0 (didn't play) | 0.36 | 17,476 |
> | 1-45 (sub/partial) | 1.13 | 3,216 |
> | 46-89 (most of game) | 2.04 | 2,575 |
> | 90 (full game) | 2.50 | 5,230 |
>
> No spike — error rises **smoothly and monotonically** with minutes played, not disproportionately in the partial-minutes zone. This is largely the expected, mundane pattern: players who play a full 90 have a much wider range of possible outcomes (0 to 20+ points via goals/assists/bonus), so naturally larger absolute error; players who don't play are close to deterministically zero, so naturally tiny error — that shape shows up whether or not a two-stage split would actually help, so on its own this isn't strong evidence *against* the mixed-distribution theory either. **No strong evidence either way; the test is likely underpowered to fully separate the two stories.** One detail is mildly reassuring, not confirmatory: the 1-45 band — despite being the messiest population (cameos, early subs, doubtful returns all mixed together) — came in comfortably *below* both the 46-89 and 90 bands, not above them, which is closer to where a severe conflation problem would show up worst if it were real. **Shelving the two-stage build** on that basis, but a sharper version of this test exists if it's ever worth revisiting: bucket by a recent-minutes-*volatility* feature instead of realized minutes, which tests the actual hypothesis (does uncertainty about playing time predict error?) rather than a proxy for it.

---

> [!IMPORTANT]
> **The cold-start fix above was declared done too early.** Auditing it turned up two players (Alisson Becker, Garnacho) showing up as false zero-history cases — the right reaction, per feedback, was to treat that as the *signature* of a systematic join bug, not two isolated flukes, and audit properly before calling it fixed: every player with real Premier League minutes last season, checked against this season's feature table for a fully zero-filled row.
>
> **Root cause found.** `simulate_season.build_predictions()` carried a player's rolling form across the season boundary by grouping on their **name string** (`combined.groupby("name")`). Names are not stable: `merged_gw.csv`'s own `second_name` field changes format between seasons for the same player (Alisson's was `"Ramses Becker"` in 2024-25, `"Becker"` in 2025-26 — confirmed via `players_raw.csv`, which also has an `element`/`id` field that's re-numbered every season, so that wasn't a safe join key either). Auditing every 2024-25 player with real minutes against 2025-26's name list: **193 of 562 (34%) had no exact-string match.** A stricter accent-normalized word-subset check (not naive fuzzy-string matching, which produced false positives like "Ashley Young" → "Ashley Barnes") confirmed **41 of those 193 were still-active, clearly-identifiable players** miscounted as debutants purely from a name-format change (`Adama Traoré` → `Adama Traoré Diarra`, `İlkay Gündoğan` vs `Ilkay Gündogan`, etc.) — real information lost on assets a manager might actually want to own, not fringe players.
>
> **Fixed properly**, not patched: `players_raw.csv` (one more file per season, now fetched by `fetch_historical_data.py` alongside `merged_gw.csv`/`fixtures.csv`/`teams.csv`) has a `code` field — FPL's actual permanent player identifier, constant across a player's whole career, unlike `element`/`id` which resets every season. `train_model.load_player_codes()` maps `element → code` for a given season; `build_predictions()` now joins prior/current season data and groups the rolling-form carry-over on `player_code`, not `name`. Verified zero unmatched rows across both 2024-25 and 2025-26 (29,338+27,231 rows, 0 missing `player_code`). Re-running the same audit by stable code instead of name: only **147 of 562** now show as "missing" (down from 193) — the 46-player gap closing is exactly the previously-hidden format-mismatch cases (the 41 found by hand, plus a few the manual check couldn't catch, e.g. Turkish dotless-ı vs regular-i in `Bayındır`/`Bayindir`). The remaining 147 are the genuine turnover — relegations, retirements, transfers abroad — expected in any real Premier League season.
>
> | Season | Before this fix | After | Diff |
> | --- | --- | --- | --- |
> | 2023-24 | 2093 | 2093 | 0 |
> | 2024-25 | 2042 | 2042 | 0 |
> | 2025-26 | 1987 | **1978** | **-9** |
>
> 2023-24 and 2024-25 are untouched — their specific prior/current season boundaries didn't happen to include one of these name-format mismatches. 2025-26 moved by a small, real amount (Alisson/Garnacho-style mismatches were specific to the 2024-25→2025-26 boundary). Multi-season results as now validated (all three fixes applied): **2023-24 → 2093 (+90 vs avg)**, **2024-25 → 2042 (+34 vs avg)**, **2025-26 → 1978 (+83 vs avg)**.

---

> [!NOTE]
> **Three more contributor PRs reviewed and merged, none of which touch decision logic — backtest confirmed unchanged (2055/2193/2049) as expected:**
>
> - **Live pipeline hardening** (`model/live_pipeline.py`) — the GW1/Wildcard ensemble fix and 5H41L3N's own follow-up review both landed here: full squad rebuilds (true GW1, or the unlimited pre-deadline rebuild before any gameweek is played) now use the 5-model ensemble instead of a single model, matching `simulate_season.py`. A certainty-only availability filter excludes only players FPL has explicitly declared out (`status = 'u'`, or `i`/`s` at 0%) from the *buy* pool — never force-sells an owned player, deliberately narrower than the probability-based filters (attempts 5/6/8) that all lost. Also found and fixed something real: the email/password login flow was completely broken, since FPL retired `users.premierleague.com` (confirmed NXDOMAIN) — replaced with a browser session-cookie flow (`FPL_COOKIE`) and a `--check-auth` mode to test login without submitting a real team.
> - **Availability news fields** — the availability fetcher was discarding `news`, `news_added`, and `ep_next` from every snapshot it already downloaded. Verified strictly additive (0 rows changed in the existing `status`/`chance_of_playing` columns). No behavior change on its own; groundwork for a future availability attempt that can finally tell "loaned out" from "suspended 3 games" from "back Jan 20th" apart, which every attempt in this plan so far has had to treat identically.
> - **xG features** — same honest-negative-result pattern as the prediction-compression investigation above. Found a real data trap first (FPL's xG columns are `0.0`, not blank, before GW16 of 2022-23 — same class of bug as the phantom-doubles fix), then showed xG is a wash-to-regression on the squad-relevant top-150 split and never ranks in the top 6 features by importance. Ships as `enable_xg_features()`, off by default — confirmed never called anywhere in the merged diff, so this is a genuine no-op unless invoked. Proposes xG-minus-actual-goals (over/underperformance vs. luck) as the untried variant worth a future look.

---

> [!NOTE]
> **PR #18 merged** ("Make the bot safe to leave running, and ship the schedule that runs it") — the double-submission bug was real and verified against the actual code before merging: `plan_transfers()`'s loop still evaluates `k=1` even when `free_transfers=0`, and the ceiling check `hits > MAX_AUTOMATED_HITS` (`1 > 1` = false) lets a duplicate transfer through if a second run lands inside the same 90-minute due window as the first. Fixed with `already_submitted(gw)`, which checks the audit log for a run that reached the end of `submit()` for that gameweek (keyed on outcome, not intent — a run that dies mid-POST leaves no "submitted" record, so a retry still fires), plus a `--force` override. Logging changed from a single pre-submit call recording `applied: bool(args.apply)` (intent) to a `status` of `rejected`/`dry-run`/`failed`/`submitted`, set only once the real outcome is known. Ships `.github/workflows/live-pipeline.yml` (cron `*/30 * * * *`, dry-run unless the `AUTO_APPLY` repo variable is `true`) and `.github/workflows/cookie-check.yml` (daily read-only auth check). Verified structurally backtest-independent (`grep -rn "live_pipeline" --include="*.py"` — nothing imports it).
>
> **Website deployed to Vercel** (`https://plfantasybot2026-27.vercel.app`), connected to this repo's GitHub integration for auto-deploy on every push to `main` — verified by pushing a commit and confirming a fresh "Ready" production deployment appeared via `vercel ls` within a minute, distinct from the manual CLI deploys used to first get the config right (`vercel.json` needed `framework: null` — the repo's root `requirements.txt` was making Vercel autodetect it as a Python project and look for a WSGI entrypoint that doesn't exist).

---

> [!NOTE]
> **Multi-strategy dashboard, differential ("gambly") strategy, player photos, and injury/status data — the GW1-week feature push, done with the season launching in 2 days.**
>
> Four strategies now run through the *same* validated engine (`model/simulate_season.py`, `model/live_pipeline.py`) with only their squad-construction knobs differing — no separate model, no separate feature set, so any difference in outcome is attributable to policy, not to four independently-tuned pipelines (`model/strategies.py`):
>
> | Strategy | Hits/week | Lookahead | Ownership tilt | 2025-26 backtest |
> | --- | --- | --- | --- | --- |
> | Balanced (flagship, unchanged) | ≤1 | 5 GW | none | **2049** |
> | Conservative (never takes a hit) | 0 | 5 GW | none | 2030 |
> | Reactive (no fixture lookahead) | ≤1 | 1 GW | none | 1979 |
> | Differential (high risk/reward) | ≤2 | 5 GW | ×1.6 at 0% owned | 1878 |
>
> Balanced reproduced its exact canonical score (2049) after the refactor, confirming the parameterization is behavior-preserving — `simulate()`'s new knobs (`transfer_margin`, `max_hits_per_gw`, `lookahead_gws`, `differential_weight`, `season`/`prior_season`) are all resolved from the module globals *inside* the function body rather than bound as literal defaults, specifically so `multi_season_backtest.py`'s existing pattern of monkeypatching `simulate_season.SEASON` before calling with no args keeps working unchanged. Model/ensemble/price-model training happens once and is shared across all four strategies in `model/run_all_strategies.py`, not once per strategy — only the cheap post-hoc reweight (`apply_differential_tilt`) and the per-gameweek decision loop differ.
>
> Differential landing lowest here is one backtest, not an average — a single realization shows the downside (more hits taken chasing marginal transfers, £ spent on low-ownership picks that didn't return) without showing the upside variance is supposed to buy. Flagged as such on the website rather than framed as "worse."
>
> **Player photos** (`https://resources.premierleague.com/premierleague/photos/players/110x140/p{code}.png`, keyed on the same stable `player_code` already carried through for the cold-start fix — no new join needed) and **ownership %** now render on every player card. **Injury/suspension status** (`status`, `news`, `chance_of_playing_next_round`) is threaded through the same `players_raw.csv` merge point (`train_model.load_player_codes()`) and shown as a badge with the news text as a tooltip — live 2026-27 squads get this genuinely point-in-time from the current bootstrap-static snapshot; backtest seasons show it display-only (vaastav's `players_raw.csv` is a single scrape per season, not a per-gameweek record, so it's "status as of whenever it was downloaded," not "status at that gameweek's deadline" — not wired into the model as a training feature for that reason, matching the caution already documented above for the availability-news-fields PR).
>
> **`model/generate_live_strategies.py`** runs all four strategies against the live FPL API for the upcoming gameweek (currently GW1) — dry run only, never submits. `model/live_pipeline.py`'s own `--apply` path is untouched by any of this: it still only ever runs the Balanced strategy's exact settings against the real account, gated by `AUTO_APPLY`/`--apply` exactly as before. The other three strategies persist their own "shadow" squad state (`data/live_state_{key}.json`, shaped like the real `/my-team/` response so `choose_team()`/`plan_transfers()` work unmodified) since there's no real FPL entry behind them. Scoring a shadow squad's held picks against real results once a gameweek finishes — the "running total" the live dashboard needs past GW1 — is not built yet; there's nothing to score until GW1 is actually played, and rushing that piece felt like the wrong place to cut a corner two days before launch. Next thing to build once GW1 has real results.
>
> **`model/snapshot_fpl_data.py`** archives bootstrap-static + fixtures every 30 minutes into this repo — our own copy of what Randdalf/fplcache does for the community, so 2026-27 doesn't depend on that archive staying online or being fine-grained enough. A full raw snapshot would be ~13,000 near-duplicate files over a season at that cadence for near-zero benefit (most 30-minute windows change nothing), so only `data/snapshots/latest.json.xz` (always overwritten) and one dated snapshot per UTC day are kept in full; a `changelog.jsonl` is appended to only when a tracked field (price, status, chance of playing, news) actually changes — finer-grained than fplcache's fixed 4x/day, since a price rise or fresh injury note lands here within 30 minutes instead of up to 6.
>
> **The manual/auto line, drawn deliberately**: `data-snapshot.yml` (pure archival, touches nothing) runs on the `*/30 * * * *` cron the user asked for. `refresh-dashboard.yml` (regenerates strategy recommendations and rebuilds the website) is `workflow_dispatch`-only — it never touches a real team either, but the user was explicit ("I would rather the team is updated manually not auto done"), and even a read-only dashboard refresh felt closer to "the team" than to raw data archival. `live-pipeline.yml`'s real submission path is unchanged: still gated by `AUTO_APPLY`/`--apply`, still only the Balanced strategy.

---

> [!NOTE]
> **Decided: transfers are being made by hand, not through `--apply`.** No `FPL_MANAGER_ID`/`FPL_COOKIE` secret was ever set up (that step needs a cookie copied from a signed-in browser, which only the user can produce — FPL retired the scriptable password-login endpoint this project used to rely on, see `live_pipeline.login()`), and rather than set that up the user confirmed the manual route directly.
>
> `live-pipeline.yml` and `cookie-check.yml` had their `schedule:` triggers removed — without a manager id, a scheduled `live-pipeline.yml` run can't see a real held squad (it would just retrain a model every 30 minutes to log a generic from-scratch build nobody reads, worse than what `generate_live_strategies.py` already produces without any credentials), and a scheduled `cookie-check.yml` would fail every single day with nothing to check. Both kept as `workflow_dispatch`-only, so either can be turned back into a cron in one line if a cookie is ever added later. `data-snapshot.yml` and `refresh-dashboard.yml` are unaffected — neither ever needed credentials.
>
> Practical result: the website is the whole workflow now. Refresh it (`refresh-dashboard.yml`, manual) whenever wanted, read whichever strategy's picks look right, enter the transfer in the FPL app by hand.

---

## Phase 5 — Automation & Interface

- [x] **Scheduled pipeline** — scrape → feature-build → predict → optimize, run automatically each gameweek before the transfer deadline. ([`model/live_pipeline.py`](model/live_pipeline.py)) Pulls the live season from the FPL API into the same four per-season files [`model/fetch_historical_data.py`](model/fetch_historical_data.py) writes, so `build_season_features`/`optimizer`/`build_horizon_scores` are reused unchanged rather than reimplemented for live. Self-gates with `--only-if-due` so one hourly cron entry covers a season of irregular kickoff times.
- [x] **Output/reporting** — [`website/build_site.py`](website/build_site.py) builds a self-contained, single-file dashboard deployed at [plfantasybot2026-27.vercel.app](https://plfantasybot2026-27.vercel.app), auto-deploying on every push. No longer a one-shot build from a single completed season: shows both the 2025-26 validated backtest and live 2026-27 picks, across all five strategies, with a leaderboard, deadline countdown, full-season deadline calendar, and an in-page Info panel explaining all of it. Refreshed on demand via `refresh-dashboard.yml`, not automatically — see the manual/auto line noted below.
- [ ] *(Optional)* **Live team sync** — authenticate against your own FPL team via `/my-team/{manager_id}/` to compare the bot's recommendation against your actual squad.
- [ ] *(Optional)* **Auto-apply transfers** — only if you're comfortable letting the bot act without a manual approval step; recommend keeping a human-in-the-loop confirmation initially.

> [!WARNING]
> Auto-applying transfers on your live FPL team is an irreversible action each gameweek. Keep a manual review/approval step until the model and optimizer have a season's worth of backtested trust behind them.

---

> [!CAUTION]
> **xG features re-tested as a real strategy (`model/run_xg_strategy.py`), and the result is not clean.** Balanced's exact policy, same engine, only the model gets xG/xA features on — but xG data doesn't exist before 2022-23, so this run trains on 3 seasons (2022-23 → 2024-25) instead of Balanced's 5 (2020-21 → 2024-25). Scored **2100** vs Balanced's **2049** — a real, reproducible number, but two variables moved at once (xG on/off, and which seasons the model ever saw), not one. The earlier isolated feature-importance check (xG never in the top 6 features, wash-to-regression on the top-150 split) is not overturned by this — it's a genuinely open question now: does xG help, does dropping the COVID-disrupted 2020-21/2021-22 seasons help, or both? Needs Balanced re-run restricted to the same 2022-23→2024-25 window before either explanation can be trusted. Deliberately shipped as its own isolated process (not folded into `strategies.py`/`run_all_strategies.py`), since `enable_xg_features()` mutates `train_model`'s feature-column globals in place and would silently change what every other strategy trains on if run in the same process.
>
> **`data/snapshots/` is now excluded from local checkouts via `git sparse-checkout` (`.git/info/sparse-checkout`, pattern mode: `/*` then `!/data/snapshots/`)** — still fully committed and growing on GitHub via the 30-minute cron, just never materializes on disk locally. Verified: `git ls-tree` still shows all 4 tracked snapshot files after the reapply, `git status` shows no phantom deletions, and the directory is genuinely absent from the working tree.

---

> [!IMPORTANT]
> **A real bug found while adding the deadline countdown: "unlimited free rebuild" was gated on the wrong condition.** `live_pipeline.py` and both live-strategy generators computed `unlimited = current is not None and not finished` — true for as long as no gameweek has been marked "finished" this season. But FPL doesn't mark a gameweek finished until its matches actually conclude, which can be days after its *deadline* passes. A run triggered in that gap would still describe the squad as freely rebuildable from scratch, when real FPL had already locked transfers at the deadline. Fixed in all three call sites by also requiring `now < deadline`. The real `--apply` submission path was already separately guarded by its own `now > deadline` check, so this wasn't a submission-safety hole — it only fixed what a dry run *described* as still possible.
>
> Built alongside the fix (since it needed the same deadline data anyway): a live-ticking countdown on the site, and a browsable dropdown of all 38 real gameweek deadlines (`data/live_gameweek_calendar.json`, written once per `generate_live_strategies.py` run from `bootstrap-static`'s `events`, not duplicated per strategy). The countdown's date format explicitly labels the viewer's timezone (`timeZoneName: 'short'`) — an earlier version showed a bare local time with no label, which read as a bug when it didn't match a UTC time quoted elsewhere in the same conversation.

---

> [!NOTE]
> **Data snapshot retention corrected to what was actually asked for, then re-corrected to what was actually sustainable.** The first version of `model/snapshot_fpl_data.py` kept one full snapshot per day forever plus a change-only log — cheap, but not what "just like fplcache" (which keeps every snapshot) actually meant. Fixed to write every single 30-minute run's full snapshot to `data/snapshots/history/{year}/{month}/{day}/{HHMM}.json.xz` (fplcache's own path convention), kept forever — then immediately walked back to a 7-day rolling window (`HISTORY_RETENTION_DAYS = 7`, pruned each run) once the real cost was stated plainly: unbounded full-resolution snapshots run ~1.6GB/year, vs ~33MB/year for the once-daily anchor. `daily/{date}.json.xz` is never pruned, so nothing is actually lost when a day ages out of `history/` — it just thins from 48 snapshots to 1.
>
> Caught a real bug writing the test for the pruning logic: `Path("1200.json.xz").stem` is `"1200.json"`, not `"1200"` — `.stem` only strips one suffix. Parsing that as minutes would have silently mis-scheduled every prune (an off-by-one-suffix bug that a quick manual check wouldn't have caught, since `"1200.json"[:2]` still happens to parse as a valid-looking hour). Fixed by reading the HHMM digits directly off `path.name` instead. Verified with a synthetic 10-day-spanning tree before shipping: exactly the snapshots older than the cutoff were removed, their emptied day-directories cleaned up, and the boundary day (exactly 7 days old) correctly kept.

---

> [!NOTE]
> **The whole website was rebuilt on a professionally-designed UI, wired to real data — not just restyled.** A design handoff (`~/Downloads/plfantasybot-ui-light/index.html`, a light Apple-inspired "glass" desktop interface) came with its own README promising the demo data objects (`seasons`, `strategies`, `squadData`, `demoDeadlines`) were cleanly separated for a data swap. In practice its rendering logic only ever showed one static squad regardless of which gameweek or strategy was "selected" — `squadData` and `strategyBase` were flat, single-snapshot objects, not something that varied with navigation. Matching the design meant keeping its HTML/CSS completely untouched and rewriting the entire `<script>` block from scratch against the same nested season → strategy → gameweek JSON shape the previous `build_site.py` already produced, so the dashboard is genuinely data-driven across 5 strategies × up to 38 gameweeks × 2 seasons, not a reskinned demo.
>
> One real data-shape gap closed in the process: player entries carry a full team name (`"Man City"`, from `merged_gw.csv`), but the design displays a compact code next to the opponent (already a short code, e.g. `"BOU (H)"`). `build_team_shortcodes()` builds a name→code map from every season's `teams.csv` and converts it in Python before embedding, rather than teaching the client-side renderer a second lookup table it would then have to keep in sync with the server-side one used for opponent codes.
>
> Verified before shipping (this size of a rewrite justified more than "it built without errors"): `node --check` on the extracted `<script>` block, every `el()`/`getElementById()` call in the JS cross-checked against actual HTML element ids (all present), and the embedded JSON scanned for zero un-converted (space-containing) team values across both seasons and all five strategies.

---

> [!NOTE]
> **A second design handoff (`plfantasybot-desktop-ui-v2.html`) corrected the pitch itself — diffed against the first handoff and only that diff was ported, not a full re-import.** `diff` against the original found the change was entirely confined to: the pitch's background-gradient direction (was `90deg`, drawing the halfway line and mowing stripes for a *landscape* pitch, when the squad grid actually stacks positions in rows for a *portrait* one — corrected to `180deg`), a richer 5-stop pitch colour, real markings added (six-yard box, penalty spot, centre spot, a proper D-arc, a goal mouth distinct from the penalty box — the original had only a bare bordered penalty box), and the starting-XI row order flipped from GKP-first to `FWD, MID, DEF, GKP` so the goalkeeper anchors the near/bottom row instead of the top one (bench order untouched — the v2 diff didn't touch it either).
>
> Several smaller fixes landed in the same stretch, each in response to a specific, concrete observation rather than speculative polish:
> - **Season-calendar dots recoloured** (amber = past, green = current, red = future) and decoupled from click state — the dot used to turn purple for whichever row was clicked to retarget the countdown, conflating "what you're browsing" with "what actually happened." "Current" is now computed once as the first not-finished gameweek in the calendar, independent of any click; only the row's background highlight still follows selection.
> - **Future gameweeks greyed out** in the calendar list, so "this hasn't happened yet" reads at a glance across all 38 rows without checking each date.
> - **Chance-of-playing % now shown directly on the status badge**, not just inside a hover-only tooltip — doubtful/injured players show the real graded percentage (e.g. "75%"); suspended/unavailable/not-in-squad keep a letter code, since those are binary (can't play, full stop) and a computed "0%" would say less than the code does.
> - **Fully-available players now show an explicit "100%" badge** instead of nothing — FPL doesn't publish an explicit 100 for fit players (`chance_of_playing` is `null`, not `100`, when there's no concern), but a blank corner read as "no data" rather than "no concern." Styled neutral, not the red used for genuine flags, so it doesn't read as a warning.
> - **An in-page Info panel** (toggled from a topbar button) explaining the dashboard, all five strategies (pulled live from real strategy metadata so the copy can't drift out of sync with the actual descriptions), every stat/chip/badge, and the calendar/countdown behaviour — added because a UI this information-dense needs a legend, not just for a first-time viewer.
> - **Footer credits and repository link** added.

---

> [!IMPORTANT]
> **GW1 2026-27 is now locked in as real, scored history — every strategy builds GW2 forward from its actual held squad, not a fresh rebuild.** Until now `generate_live_strategies.py`/`generate_live_xg_strategy.py` only ever wrote a single-entry `gameweeks` array, overwritten each run with a fresh "not yet played" squad — there was no path from "predicted GW1 squad" to "what actually happened in GW1." Closed by:
> - **Provisional-finish detection** (`provisionally_finished_gws()`): FPL's own `event.finished`/`data_checked` flags lag real match completion by up to a day or more — confirmed live, all 10 GW1 fixtures showed `finished_provisional: true` with real final scores while `event.finished` was still `false`. Real results are synced and squads scored the moment every fixture in a gameweek is provisionally final, not days later once FPL's own bookkeeping catches up. `next_planning_gameweek()` uses the same signal to correctly target GW2 once GW1 is done.
> - **Real-results scoring** (`real_outcome`/`apply_auto_subs`/`score_gameweek_entry`), mirroring `simulate_season.py`'s backtest rules but operating on saved JSON player-dicts instead of DataFrames (a live squad only exists as JSON between separate script runs): auto-subs for blanked starters (formation-legality-checked), effective-captain fallback when the captain didn't play, Bench Boost bench-scoring, -4 per hit.
> - **`gameweeks` now accumulates real history** instead of being overwritten — each run scores any finished gameweek not yet scored, then appends the newly-planned gameweek, keyed and sorted by `gw`.
> - **A real bug this surfaced immediately**: squads saved before `element` was added to `live_player_entry()`'s/`player_entry()`'s output only carried `photo_code`, so the first real scoring run crashed with `KeyError: 'element'`. Fixed with `backfill_element_ids()`, resolving `element` from `photo_code` (the player's permanent `code`) via the live bootstrap's code->id map — handles old and new saved data alike, no separate migration script needed.
> - **Two site bugs fixed proactively before the real run**, since the new multi-gameweek shape would otherwise have silently exposed them: the leaderboard was reading the literal last `gameweeks` entry rather than the last *scored* one (wrong once an unplayed future gameweek is appended after a real one), and the dashboard always defaulted to GW1 instead of the most recent gameweek.
>
> Real GW1 result, first time run: Balanced 33 pts, Conservative 33 pts, Reactive 32 pts, Differential 32 pts, xG Experimental 31 pts. All five then planned GW2 via normal transfer rules (1 free transfer used, 0 hits) against their real GW1 squads.

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
