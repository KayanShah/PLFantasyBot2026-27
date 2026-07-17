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

## Phase 5 — Automation & Interface

- [ ] **Scheduled pipeline** — scrape → feature-build → predict → optimize, run automatically each gameweek before the transfer deadline.
- [x] **Output/reporting** — [`website/build_site.py`](website/build_site.py) builds a self-contained, single-file website showing the bot's pick for every gameweek (pitch view, opponent, difficulty colour-coding, captaincy, chips) from the best validated simulation run. Currently a one-shot build from a completed season's log, not yet a live weekly report — see the scheduled-pipeline item above.
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
