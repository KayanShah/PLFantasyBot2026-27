# PLFantasyBot — Research Notes

Research into building a bot that selects the best Fantasy Premier League (FPL) team using historical data, live stats, and predictive modeling.

---

## 1. Goal

Build a system that:

1. **Predicts** each player's expected points for upcoming gameweek(s).
2. **Optimizes** squad/lineup selection under FPL's budget and formation rules.
3. **Plans transfers and chip usage** across the season, not just gameweek-by-gameweek.

This splits into three largely independent problems: **data**, **prediction**, and **optimization**.

---

## 2. Data Sources

### 2.1 Official Fantasy Premier League API (free, no key required)

Base URL: `https://fantasy.premierleague.com/api/`

There's no official public documentation — everything below is reverse-engineered by the community, but it's stable and widely relied upon.

| Endpoint | Returns |
|---|---|
| `/bootstrap-static/` | The core payload: all players (`elements`), teams, gameweeks (`events`), and their season-to-date stats. Start here. |
| `/fixtures/` | Every fixture this season, with difficulty ratings (FDR). |
| `/fixtures/?event={gw_id}` | Fixtures for a single gameweek. |
| `/element-summary/{player_id}/` | Per-player fixture-by-fixture history and past-season summaries. |
| `/event/{gw_id}/live/` | Live per-player stats (points, minutes, bonus, BPS) for a gameweek. |
| `/entry/{manager_id}/` | A manager's team summary. |
| `/entry/{manager_id}/history/` | A manager's season history + chips used. |
| `/entry/{manager_id}/event/{gw_id}/picks/` | A manager's squad for a specific gameweek. |
| `/leagues-classic/{league_id}/standings/` | Classic mini-league standings. |
| `/leagues-h2h-matches/league/{league_id}/` | Head-to-head league match results. |
| `/event-status/` | Whether bonus points / league scores are finalized for a gameweek. |
| `/dream-team/{gw_id}/` | The gameweek's highest-scoring XI. |
| `/team/set-piece-notes/` | Penalty/corner/free-kick taker notes per club (useful signal for xG-adjacent players). |
| `/my-team/{manager_id}/` | Authenticated-only: current picks, transfers, chip status. |

Notes:
- No API key needed for read-only public endpoints; auth (session cookie) is only required for endpoints tied to *your own* team (`/my-team/`, making transfers, etc.).
- CORS-blocked from a browser — fine to call server-side.
- `bootstrap-static` includes each player's `ep_next` (FPL's own expected-points estimate) and ICT index components (Influence, Creativity, Threat), which are useful baseline features even before building a custom model.
- Undocumented and can change without notice — build a thin wrapper layer so breakage is isolated.

Community wrappers worth using instead of hand-rolling HTTP calls: the [`fpl` Python package](https://fpl.readthedocs.io/) (async, covers most endpoints) or a lightweight `requests` client.

### 2.2 Historical Data

- **[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League)** — the standard historical dataset. Season-by-season and gameweek-by-gameweek CSVs of every player's points, minutes, ICT stats, etc., going back several seasons. Ideal for training a points-prediction model since the live API only exposes the current season well.

### 2.3 Advanced Underlying Stats (xG, xA, etc.)

- **[Understat](https://understat.com/)** — xG, npxG, xA, xG-chain/xG-buildup per player/team for the Premier League and other top leagues. No official API; scraped via community tools (e.g. Apify actors) or the `understat` Python package. Strong signal for regression/progression (over/under-performing xG suggests points are due to rise or fall).
- **FBref** — historically a major source of advanced stats (Opta-sourced), but its advanced-stats pages were shut down in Jan 2026, so treat as unreliable going forward; don't build a pipeline that depends on it long-term.

### 2.4 Fixtures, Odds & Team-Strength Context

- **[football-data.org](https://www.football-data.org/)** — free tier, results/fixtures/standings for Premier League + 11 other competitions, 10 req/min, no card required. Good as a secondary fixtures/results source or cross-check against the FPL API.
- **API-Football** — free tier (100 req/day) or cheap paid tier ($19/mo, 7,500 req/day), broader competition coverage; useful if we outgrow football-data.org's rate limit.
- **Betting odds** (match result, over/under, BTTS, anytime-scorer) are a strong external signal — bookmaker prices bake in team news, form, and injuries faster than most models can. Historical odds datasets exist back to 2000/01; live odds would need an odds-API subscription (e.g. The Odds API).
- Enterprise-grade options (Opta/Stats Perform, Sportradar, Genius Sports, Sportmonks) exist but are paid and overkill for a hobby project — worth knowing about only if the bot ever needs official rights-cleared data.

### 2.5 Suggested Data Stack for v1

1. FPL API — live prices, ownership, fixtures, current-season points (primary).
2. vaastav historical CSVs — model training set.
3. Understat — xG/xA features to smooth over small-sample noise in raw points.
4. football-data.org — fixture/result cross-check and fallback.
5. (Later) odds data — as an additional predictive feature once the core pipeline works.

---

## 3. Predicting Player Points

Common approaches, roughly in order of complexity:

1. **Baseline heuristics** — form (points over last N games), minutes-played trend, fixture difficulty rating (FDR), price-to-points ratio. Cheap to compute, decent baseline to compare models against.
2. **FPL's own `ep_next`** — already computed for every player in `bootstrap-static`; useful as a benchmark/feature, not as the final answer (it's known to lag real form and matchup context).
3. **Statistical/regression models** — predict expected points from features like xG90, xA90, minutes probability, fixture difficulty, home/away, opponent defensive strength. Simple linear/Poisson regression on goals+assists, converted to FPL points via the scoring rules, works surprisingly well as a mid-tier model.
4. **Gradient-boosted trees** (XGBoost, LightGBM, CatBoost) — trained on the historical gameweek dataset, standard choice in published FPL-optimization papers and open-source projects (e.g. OpenFPL, arXiv:2505.02170). Handles non-linear interactions (fixture congestion, rotation risk, home/away splits) better than linear models.
5. **Minutes-played model as a separate sub-problem** — a lot of FPL prediction error comes from not knowing who starts, not from misjudging quality. Worth modeling start probability separately (e.g. from team news / press conferences / prior rotation patterns) and multiplying into the points model.

Reference project: **[OpenFPL](https://arxiv.org/html/2508.09992v1)** — an open-source forecasting method claiming to rival paid commercial FPL prediction services (FPL Review's "Massive Data Model"), with trained models and inference code released under MIT license. Good starting point to benchmark against rather than starting from scratch.

---

## 4. Squad/Lineup Optimization

Given predicted points per player, picking the best legal squad is a classic **constrained optimization / knapsack problem**, almost universally solved with **Integer Linear Programming (ILP)**.

### FPL constraints to encode

- 15-player squad: 2 GK, 5 DEF, 5 MID, 3 FWD.
- Starting XI of 11 from that squad, with valid formation (min 1 GK, min 3 DEF, min 2 MID... — subject to whichever formation rules apply that season), plus a bench order.
- £100m budget (season starting budget; adjusts with player price rises/falls).
- Max 3 players from any single real-world club.
- Captain (2x points) and vice-captain (fallback if captain doesn't play) selection.
- Transfer constraints: 1 free transfer per gameweek (rules vary — 2025/26 introduced changes worth re-checking each season), extra transfers cost -4 points each, unless a chip is active.
- Chips: **Wildcard** (unlimited free transfers for a gameweek), **Free Hit** (temporary unlimited transfers, reverts next GW), **Bench Boost** (bench points count), **Triple Captain** (captain scores 3x instead of 2x). Note: 2025/26 season introduced **two sets of each chip** (one usable in each half of the season).

### Tooling

- **[sertalpbilal/FPL-Optimization-Tools](https://github.com/sertalpbilal/FPL-Optimization-Tools)** — the most mature open-source implementation. Uses `pandas` + `sasoptpy` to build the ILP model and solves with HiGHS (`highspy`, free/open-source solver — no commercial license needed, unlike CPLEX/Gurobi). Supports multi-gameweek transfer planning, chip timing, and hitting (-4) decisions, not just single-gameweek squad picks. Strong reference implementation to adapt rather than reinvent.
- **[dbozbay/FPL-Optimization](https://github.com/dbozbay/FPL-Optimization)** — simpler linear-programming optimizer, good as a readable starting point.
- Academic reference: *"Data-Driven Team Selection in Fantasy Premier League Using Integer Programming and Predictive Modeling"* ([arXiv:2505.02170](https://arxiv.org/pdf/2505.02170)) — formalizes the ILP formulation and combines it with ML-predicted points; useful as a spec to implement against.
- Solver choice: **HiGHS** (open-source, fast enough for this problem size) is the practical default. Google OR-Tools' CP-SAT is another solid free alternative.

### Multi-gameweek planning (the harder, more valuable part)

Picking the best team for *one* gameweek is straightforward ILP. The real value is in:
- Planning transfers several gameweeks ahead (e.g. don't sell a player about to have 3 good fixtures for one with 3 bad ones).
- Deciding **when** to take a -4 hit vs. bank a free transfer.
- Deciding **when** to play each chip, especially around double/blank gameweeks (when some clubs play twice or not at all in a given week due to cup fixture rearrangement).
- This is modeled as a rolling-horizon ILP (optimize over the next N gameweeks, re-solve each week as news comes in) — exactly what sertalpbilal's tools already do.

General chip-timing heuristics from community strategy guides (useful as sanity checks against whatever the optimizer outputs, not a replacement for it):
- Wildcard: commonly played once fixtures/form data becomes reliable (~GW7–12) and again before a fixture swing or the festive schedule congestion.
- Bench Boost: best value in a double gameweek, often right after a Wildcard when the whole squad (including bench) is freshly optimized.
- Free Hit: best for blank gameweeks or when 3+ of your starters don't play; not worth it for 1–2 missing players (just bench them).
- Triple Captain: save for a premium player's best single/double gameweek matchup.

---

## 5. Proposed Architecture

```
┌─────────────────┐     ┌───────────────────┐     ┌────────────────────┐
│   Data Layer     │ --> │  Prediction Layer  │ --> │  Optimization Layer │
│ FPL API, Vaastav │     │ ML model → expected │     │  ILP (HiGHS) →      │
│ historical CSVs, │     │ points per player   │     │  squad + captain +  │
│ Understat, odds  │     │ per gameweek        │     │  transfer/chip plan │
└─────────────────┘     └───────────────────┘     └────────────────────┘
```

- **Data layer**: scheduled pull from FPL API (prices/ownership/fixtures change daily-ish), periodic refresh of Understat xG data, one-time load + incremental update of historical CSVs.
- **Prediction layer**: start with a gradient-boosted-tree model trained on historical data + current-season features; benchmark against FPL's own `ep_next` and simple heuristics before trusting it.
- **Optimization layer**: adapt sertalpbilal's ILP approach for both single-gameweek squad selection and rolling multi-gameweek transfer/chip planning.

---

## 6. Open Questions / Next Steps

- Confirm current-season (2025/26+) exact transfer and chip rules from the official FPL site before hardcoding constraints (they changed for 2025/26 — two chip sets — and could change again).
- Decide on a betting-odds provider (and budget) if we want that signal beyond the free historical dataset.
- Decide model retraining cadence — weekly during the season, using rolling gameweek results, is standard.
- Prototype the ILP layer first against the *official* `ep_next` values (zero model-building required) to validate the optimization pipeline end-to-end, then swap in a custom prediction model.

---

## 7. Key References

- [Oliver Looney — FPL APIs Explained](https://www.oliverlooney.com/blogs/FPL-APIs-Explained)
- [FPL Python client docs](https://fpl.readthedocs.io/en/latest/user/quickstart.html)
- [vaastav/Fantasy-Premier-League (historical data)](https://github.com/vaastav/Fantasy-Premier-League)
- [sertalpbilal/FPL-Optimization-Tools](https://github.com/sertalpbilal/FPL-Optimization-Tools)
- [dbozbay/FPL-Optimization](https://github.com/dbozbay/FPL-Optimization)
- [OpenFPL — open-source FPL forecasting](https://arxiv.org/html/2508.09992v1)
- [Data-Driven Team Selection in FPL Using Integer Programming (arXiv:2505.02170)](https://arxiv.org/pdf/2505.02170)
- [Understat](https://understat.com/)
- [football-data.org](https://www.football-data.org/)
- [FPL Chip Strategy Guide — Fantasy Football Scout](https://www.fantasyfootballscout.co.uk/2026/04/09/the-ultimate-fpl-chip-strategy-guide-for-all-16-scenarios)
- [Premier League — What's new in 2025/26 Fantasy: Two sets of chips](https://www.premierleague.com/en/news/4362027/whats-new-in-202526-fantasy-two-sets-of-chips)
