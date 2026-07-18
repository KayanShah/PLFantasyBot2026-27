"""
Simulates managing an FPL team through the entire 2025-26 season, gameweek
by gameweek, using only a model trained on 2020-21 -> 2024-25 (the model
never sees 2025-26 results during training).

For each gameweek, the bot only ever uses predicted points built from
*prior* gameweeks' data to pick its squad, transfers, starting XI, and
captain — then the actual real-world result for that gameweek (from the
historical dataset) determines the score, exactly as it would for a real
manager.

Squad-construction decisions (initial squad, wildcard, transfers) value
players over the next LOOKAHEAD_GWS gameweeks, not just the immediate one
— using frozen current-form features combined with each future week's
already-published fixture (no result lookahead, just schedule facts).
Starting XI and captaincy stay single-gameweek, since you always want
your best lineup *this* week regardless of the run of form ahead.

Rules encoded (see FantasyRules.md):
  - 15-man squad: 2 GKP / 5 DEF / 5 MID / 3 FWD, <=3 players/club, £100.0m budget.
  - 1 free transfer/gameweek, rolling over up to 5; extra transfers cost -4 each.
  - Wildcard: always 2 per season (one per half), every season. Bench Boost and
    Triple Captain: 2 per season (one per half) ONLY from 2025/26 onward — every
    prior season only had 1 of each for the whole season, not 1 per half (see
    SEASONS_WITH_SECOND_CHIP_SET; verified against the official rule-change
    announcement before fixing a bug where older seasons were simulated with
    twice their real chip allowance). Free Hit is not simulated at all, any season.
  - Captain 2x, Triple Captain 3x, auto-subs for starters who didn't play.

Chip schedule (fixed heuristic, avoids GW clashes):
  Wildcard 1  -> GW8    Bench Boost 1 -> GW9 (every season)
  Wildcard 2  -> GW20   Bench Boost 2 -> GW21 (2025/26+ only)
  Triple Captain: played on the first gameweek in its eligibility window (the
  whole season pre-2025/26, each half from 2025/26) where the best available
  captain option is a forward with an easy fixture (FDR <= 2), per the "best
  strikers, easy game" brief — falls back to the best available option on the
  window's last gameweek if never triggered, so it isn't wasted.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

import train_model
from optimizer import pick_captains, pick_starting_xi, select_squad

SEASON = "2025-26"
PRIOR_SEASON = "2024-25"
STARTING_BUDGET = 1000  # £100.0m, in tenths

# Wildcard has always been 2-per-season (one per half). Free Hit, Bench Boost,
# and Triple Captain getting a SECOND copy is new for 2025/26 — every prior
# season only had 1 of each for the whole season, not 1 per half. Verified
# against https://www.premierleague.com/en/news/4362027 before fixing this;
# simulating 2 of each for older seasons overstated the bot's edge there.
SEASONS_WITH_SECOND_CHIP_SET = {"2025-26"}

WILDCARD_GWS = {8, 20}
HALF1_LAST_GW = 19
SEASON_LAST_GW = 38

# How many gameweeks ahead squad-construction decisions (initial squad,
# wildcard, transfers) look when valuing a player — so the bot doesn't sell
# someone right before an easy run, or buy into a run of hard fixtures.
LOOKAHEAD_GWS = 5

# Each future gameweek's contribution to a lookahead score is weighted by
# LOOKAHEAD_DECAY ** h (h = weeks ahead), so a -4 hit needs a clearer,
# closer-in payoff to be worth it — without this, summing flat predictions
# over 5 weeks made marginal gains look bigger than they really are and led
# to over-aggressive hit-taking (see plan.md Phase 4).
LOOKAHEAD_DECAY = 0.85

# GW1 and Wildcard squad-construction decisions were found to be decided by
# sub-1-point margins between hundreds of near-tied 15-player combinations
# (median next-best-alternative gap ~0.03-0.04 across all 9 tested instances,
# 3 seasons — see plan.md Phase 4). A single model's idiosyncratic prediction
# noise can tip that tie-break onto a materially different squad that then
# compounds for the rest of the season. Only these two full-rebuild decision
# points use an ensemble average of several independently-trained models;
# ordinary transfer weeks keep using the single canonical model, since their
# gaps are much less uniformly razor-thin (44% under the same noise-sized gap,
# but a real tail out past 11 points where a single model's call is trustworthy).
ENSEMBLE_EXTRA_SEEDS = [101, 102, 103, 104]

# Ordinary transfer weeks showed a real split in how decisive the choice was:
# 44% of tested decisions had a next-best-alternative gap under 0.74 points (the
# same order of magnitude as pure model noise), but a genuine tail out past 11
# points where a transfer's advantage over holding was clear-cut. Unlike GW1/
# Wildcard, "hold" is always a real, well-defined alternative here, so a margin
# is the right tool: only take a transfer if it beats holding by more than this
# many points, not just "any amount at all." Size empirically (see plan.md Phase
# 4 for the sweep), not from first principles — 0.0 preserves prior behavior.
TRANSFER_MARGIN = 0.0

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "season_2025-26_simulation.csv"
SQUADS_OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "season_2025-26_squads.json"
TEAMS_PATH = Path(__file__).resolve().parent.parent / "data" / "historical" / SEASON / "teams.csv"


def load_team_names() -> dict[int, str]:
    teams = pd.read_csv(TEAMS_PATH, encoding="utf-8", encoding_errors="ignore")
    return dict(zip(teams["id"], teams["short_name"]))


def ensemble_predict(models: list, X: pd.DataFrame) -> np.ndarray:
    """Mean prediction across models. A single-model list is just that model's own prediction."""
    return np.mean([m.predict(X) for m in models], axis=0)


def build_predictions(models: list) -> pd.DataFrame:
    """
    Builds a predicted_points column for every 2025-26 (player, gameweek) row,
    using rolling form carried over from the end of 2024-25 for early-season
    gameweeks (so GW1 predictions reflect real known form, not zero-knowledge —
    the model itself still never trains on any 2025-26 result). `models` is a
    list so callers can pass either the single canonical model or an ensemble
    (see GW1/Wildcard handling in simulate() — full-squad-rebuild decisions turned
    out to be decided by sub-1-point margins between hundreds of near-tied 15-player
    combinations, so a single model's idiosyncratic wobble on one player could tip
    the whole rest of the season onto a different, compounding path; averaging
    several independently-trained models makes that specific tie-break less arbitrary).
    """
    prior = train_model.load_season(PRIOR_SEASON)
    current = train_model.load_season(SEASON)
    combined = pd.concat([prior, current], ignore_index=True)

    # Join each row to its stable player_code (not element, which is re-numbered
    # every season, and not name, which can change format season to season —
    # see load_player_codes). Without this, a player whose name string changed
    # between PRIOR_SEASON and SEASON silently loses their carried-over rolling
    # form and gets treated as a zero-history debutant instead.
    codes = pd.concat([
        train_model.load_player_codes(PRIOR_SEASON).assign(season=PRIOR_SEASON),
        train_model.load_player_codes(SEASON).assign(season=SEASON),
    ], ignore_index=True)
    combined = combined.merge(codes, on=["season", "element"], how="left")
    # Fall back to a season-scoped synthetic code for the rare row with no
    # players_raw.csv match, so it degrades to old (name-less) behavior for
    # just that row rather than losing it or crashing the join.
    missing = combined["player_code"].isna()
    if missing.any():
        combined.loc[missing, "player_code"] = (
            "unmatched_" + combined.loc[missing, "season"] + "_" + combined.loc[missing, "element"].astype(str)
        )

    season_order = {PRIOR_SEASON: 0, SEASON: 1}
    combined["season_order"] = combined["season"].map(season_order)
    combined = combined.sort_values(["player_code", "season_order", "GW"]).reset_index(drop=True)
    grouped = combined.groupby("player_code", group_keys=False)

    for stat in train_model.ROLLING_STATS:
        for window in train_model.ROLLING_WINDOWS:
            col = f"{stat}_avg{window}"
            combined[col] = grouped[stat].transform(
                lambda s, w=window: s.shift(1).rolling(w, min_periods=1).mean()
            )
            # Rookies/promoted-team players/fresh arrivals have no rolling history yet.
            # Filling with 0 told the model "this player never plays" — an input the
            # model never actually trained on, since prepare() drops exactly these
            # no-history rows during training (train_model.py). Fall back to the
            # position's average instead: a reasonable "unknown, treat as an average
            # player of this position" prior until real form accumulates.
            position_avg = combined.groupby("position")[col].transform("mean")
            combined[col] = combined[col].fillna(position_avg).fillna(0)

    combined = pd.get_dummies(combined, columns=["position"], prefix="position")
    for col in ["position_DEF", "position_FWD", "position_GKP", "position_MID"]:
        if col not in combined.columns:
            combined[col] = 0
    combined["position_label"] = (
        combined[["position_GKP", "position_DEF", "position_MID", "position_FWD"]]
        .idxmax(axis=1).str.replace("position_", "", regex=False)
    )

    rows = combined[combined["season"] == SEASON].copy()
    rows["predicted_points"] = ensemble_predict(models, rows[train_model.FEATURE_COLUMNS])
    return rows


def build_horizon_scores(models: list, predictions: pd.DataFrame, gw: int, horizon: int) -> pd.Series:
    """
    For every player present at gameweek `gw`, sums a *decayed* predicted
    score across gw .. gw+horizon-1 (week h out contributes LOOKAHEAD_DECAY**h
    of its raw prediction — a confidence discount, since a prediction 4 weeks
    out is far less trustworthy than this week's). Each future week's
    prediction reuses the player's rolling-form features exactly as known at
    `gw` (frozen — no peeking at results that haven't happened yet), but
    plugs in that future week's real fixture (home/away, FDR difficulty) —
    which, unlike results, is public knowledge from the published fixture
    list. This is what makes it a fair lookahead: schedule facts, not outcomes.
    """
    base = predictions[predictions["GW"] == gw].set_index("element")
    if base.empty:
        return pd.Series(dtype=float)

    totals = base["predicted_points"].copy()
    frozen_features = base[train_model.FEATURE_COLUMNS].copy()

    for h in range(1, horizon):
        future = predictions[predictions["GW"] == gw + h].set_index("element")
        common = frozen_features.index.intersection(future.index)
        if len(common) == 0:
            continue
        feat = frozen_features.loc[common].copy()
        feat["was_home"] = future.loc[common, "was_home"]
        feat["difficulty"] = future.loc[common, "difficulty"]
        preds = pd.Series(ensemble_predict(models, feat[train_model.FEATURE_COLUMNS]), index=common)
        totals = totals.add(preds * (LOOKAHEAD_DECAY ** h), fill_value=0)

    return totals


def with_horizon_points(models: list, predictions: pd.DataFrame, gw: int, gw_pool: pd.DataFrame) -> pd.DataFrame:
    """gw_pool with predicted_points replaced by the lookahead-summed score, for squad-construction decisions."""
    horizon_scores = build_horizon_scores(models, predictions, gw, LOOKAHEAD_GWS)
    pool = gw_pool.copy()
    pool["predicted_points"] = pool["element"].map(horizon_scores).fillna(pool["predicted_points"])
    return pool


def sell_value(purchase_price: float, current_value: float) -> float:
    """
    FPL's real sell-price rule: a price *rise* since purchase is only half
    refunded (rounded down), not paid out in full — selling is not perfectly
    reversible. A price *fall* is passed on in full (no penalty). Values are
    in tenths of a million, so integer division rounds down correctly.
    """
    if current_value <= purchase_price:
        return current_value
    profit = current_value - purchase_price
    return purchase_price + profit // 2


def squad_value(current_squad: pd.DataFrame, gw_pool: pd.DataFrame) -> int:
    """Live market value of the squad (for display/logging) — NOT what selling it would raise."""
    live_prices = gw_pool.set_index("element")["value"]
    fallback_prices = current_squad.set_index("element")["value"]
    total = 0.0
    for element in current_squad["element"]:
        total += live_prices[element] if element in live_prices.index else fallback_prices[element]
    return int(round(total))


def squad_sell_value(current_squad: pd.DataFrame, gw_pool: pd.DataFrame) -> int:
    """Total funds available for a rebuild: what selling every owned player would actually raise."""
    live_prices = gw_pool.set_index("element")["value"]
    total = 0.0
    for _, row in current_squad.iterrows():
        current_value = live_prices[row["element"]] if row["element"] in live_prices.index else row["value"]
        total += sell_value(row["purchase_price"], current_value)
    return int(round(total))


def with_sell_cost(pool: pd.DataFrame, current_squad: pd.DataFrame, gw_pool: pd.DataFrame) -> pd.DataFrame:
    """pool with a `cost` column: retained players priced at sell value, everyone else at live buy value."""
    live_prices = gw_pool.set_index("element")["value"]
    purchase_prices = dict(zip(current_squad["element"], current_squad["purchase_price"]))
    pool = pool.copy()

    def cost(row):
        if row["element"] in purchase_prices:
            current_value = live_prices[row["element"]] if row["element"] in live_prices.index else row["value"]
            return sell_value(purchase_prices[row["element"]], current_value)
        return row["value"]

    pool["cost"] = pool.apply(cost, axis=1)
    return pool


def carry_purchase_prices(new_squad_ids: set[int], prior_squad: pd.DataFrame | None, gw_pool: pd.DataFrame) -> dict:
    """Retained players keep their original purchase price; newly bought players are priced at today's buy value."""
    prior_prices = dict(zip(prior_squad["element"], prior_squad["purchase_price"])) if prior_squad is not None else {}
    live_prices = gw_pool.set_index("element")["value"]
    return {
        element: prior_prices[element] if element in prior_prices else float(live_prices.get(element, 0))
        for element in new_squad_ids
    }


def attach_this_week(squad_ids: set[int], current_squad: pd.DataFrame, gw_pool: pd.DataFrame) -> pd.DataFrame:
    """Resolves a squad's rows for this gameweek's predictions; blanked players get 0 predicted points."""
    in_pool = gw_pool[gw_pool["element"].isin(squad_ids)]
    missing_ids = squad_ids - set(in_pool["element"])
    if missing_ids:
        fallback = current_squad[current_squad["element"].isin(missing_ids)].copy()
        fallback["predicted_points"] = 0.0
        fallback["difficulty"] = 5
        fallback["total_points"] = 0
        fallback["minutes"] = 0
        in_pool = pd.concat([in_pool, fallback], ignore_index=True)
    return in_pool


def real_outcome(element: int, gw_pool: pd.DataFrame) -> tuple[float, float]:
    row = gw_pool[gw_pool["element"] == element]
    if row.empty:
        return 0.0, 0.0
    return float(row.iloc[0]["total_points"]), float(row.iloc[0]["minutes"])


def apply_auto_subs(xi: pd.DataFrame, bench: pd.DataFrame, gw_pool: pd.DataFrame) -> list[int]:
    final_ids = list(xi["element"])
    positions = dict(zip(xi["element"], xi["position_label"]))

    for _, starter in xi.iterrows():
        _, mins = real_outcome(starter["element"], gw_pool)
        if mins > 0:
            continue
        for _, sub in bench.iterrows():
            if sub["element"] in final_ids:
                continue
            _, sub_mins = real_outcome(sub["element"], gw_pool)
            if sub_mins == 0:
                continue
            trial = [positions[e] if e != starter["element"] else sub["position_label"] for e in final_ids]
            counts = {p: trial.count(p) for p in ["GKP", "DEF", "MID", "FWD"]}
            if counts.get("GKP", 0) == 1 and counts.get("DEF", 0) >= 3 and counts.get("MID", 0) >= 2 and counts.get("FWD", 0) >= 1:
                final_ids[final_ids.index(starter["element"])] = sub["element"]
                positions[sub["element"]] = sub["position_label"]
                break
    return final_ids


def player_entry(
    row: pd.Series, gw_pool: pd.DataFrame, team_names: dict[int, str],
    captain_id, vice_id, effective_captain_id, tc_this_week: bool,
) -> dict:
    points, minutes = real_outcome(row["element"], gw_pool)
    opponent = team_names.get(row.get("opponent_team"), "—")
    venue = "H" if row.get("was_home") == 1 else "A"
    return {
        "name": row["name"],
        "team": row["team"],
        "position": row["position_label"],
        "opponent": f"{opponent} ({venue})" if row.get("opponent_team") else "-",
        "difficulty": int(row["difficulty"]) if pd.notna(row.get("difficulty")) else None,
        "points": int(points),
        "played": bool(minutes > 0),
        "is_captain": bool(row["element"] == captain_id),
        "is_vice_captain": bool(row["element"] == vice_id),
        "is_effective_captain": bool(row["element"] == effective_captain_id),
        "is_triple_captain": bool(tc_this_week and row["element"] == effective_captain_id),
    }


def simulate(
    model=None, predictions: pd.DataFrame | None = None, quiet: bool = False,
    ensemble_models: list | None = None, predictions_ensemble: pd.DataFrame | None = None,
) -> float:
    log_print = (lambda *a, **k: None) if quiet else print

    if model is None:
        log_print("Training model on 2020-21 -> 2024-25 (2025-26 never used in training)...")
        model = train_model.train_baseline_model()

    if predictions is None:
        log_print("Building week-by-week 2025-26 predictions (rolling form only, no lookahead)...")
        predictions = build_predictions([model])

    if ensemble_models is None:
        log_print(f"Training {1 + len(ENSEMBLE_EXTRA_SEEDS)}-model ensemble for GW1/Wildcard squad construction...")
        ensemble_models = [model] + [train_model.train_baseline_model(seed=s) for s in ENSEMBLE_EXTRA_SEEDS]

    if predictions_ensemble is None:
        predictions_ensemble = build_predictions(ensemble_models)

    fwd_threshold = predictions.loc[predictions["position_label"] == "FWD", "predicted_points"].quantile(0.90)
    log_print(f"Triple Captain trigger threshold (90th percentile FWD prediction): {fwd_threshold:.2f}\n")

    has_second_chip_set = SEASON in SEASONS_WITH_SECOND_CHIP_SET
    bench_boost_gws = {9, 21} if has_second_chip_set else {9}
    log_print(f"Chip rules this season: {'two sets (2025/26+)' if has_second_chip_set else 'one set (pre-2025/26)'}\n")

    team_names = load_team_names()
    gameweeks = sorted(predictions["GW"].unique())
    current_squad = None
    free_transfers = 1
    tc_used = {1: False, 2: False}
    season_total = 0
    log = []
    squads_log = []

    for gw in gameweeks:
        gw_pool = predictions[predictions["GW"] == gw].copy()
        if gw_pool.empty:
            continue

        hits = 0
        chip = None
        horizon_pool = with_horizon_points([model], predictions, gw, gw_pool)

        if gw == 1:
            horizon_pool_ensemble = with_horizon_points(ensemble_models, predictions_ensemble, gw, gw_pool)
            squad = select_squad(horizon_pool_ensemble, budget=STARTING_BUDGET)
            transfers_made = 0
        elif gw in WILDCARD_GWS:
            budget = squad_sell_value(current_squad, gw_pool)
            horizon_pool_ensemble = with_horizon_points(ensemble_models, predictions_ensemble, gw, gw_pool)
            priced_pool = with_sell_cost(horizon_pool_ensemble, current_squad, gw_pool)
            squad = select_squad(priced_pool, budget=budget, cost_col="cost")
            transfers_made = None
            chip = "Wildcard"
        else:
            budget = squad_sell_value(current_squad, gw_pool)
            priced_pool = with_sell_cost(horizon_pool, current_squad, gw_pool)
            current_ids = set(current_squad["element"])
            current_horizon = attach_this_week(current_ids, current_squad, horizon_pool)
            hold_net = current_horizon["predicted_points"].sum()
            best_k, best_net, best_squad = 0, hold_net, current_squad
            for k in range(1, min(free_transfers + 2, 5) + 1):
                candidate = select_squad(priced_pool, budget=budget, current_ids=current_ids, max_changes=k, cost_col="cost")
                if candidate is None:
                    continue
                # Judge the transfer by its lookahead value (worth a hit only if the gain
                # over the next LOOKAHEAD_GWS gameweeks outweighs the -4), not just this week.
                resolved = attach_this_week(set(candidate["element"]), current_squad, horizon_pool)
                net = resolved["predicted_points"].sum() - 4 * max(0, k - free_transfers)
                if net > best_net:
                    best_k, best_net, best_squad = k, net, candidate
            # Only actually take a transfer if it clears TRANSFER_MARGIN over holding —
            # "hold" is always a well-defined alternative here (unlike GW1/Wildcard),
            # so a stability margin is the right tool for this decision specifically.
            if best_k != 0 and best_net <= hold_net + TRANSFER_MARGIN:
                best_k, best_net, best_squad = 0, hold_net, current_squad
            squad = best_squad
            transfers_made = best_k
            hits = max(0, best_k - free_transfers)
            if gw in bench_boost_gws:
                chip = "Bench Boost"

        squad_ids = set(squad["element"])
        squad_resolved = attach_this_week(squad_ids, current_squad if current_squad is not None else squad, gw_pool)
        xi, bench = pick_starting_xi(squad_resolved)
        captain_id, vice_id = pick_captains(xi)

        # Triple Captain: online decision using only this gameweek's predictions.
        # Pre-2025/26 seasons only ever had one Triple Captain for the whole season
        # (see SEASONS_WITH_SECOND_CHIP_SET) — treat the whole season as "half 1" then.
        half = (1 if gw <= HALF1_LAST_GW else 2) if has_second_chip_set else 1
        half_end = (HALF1_LAST_GW if half == 1 else SEASON_LAST_GW) if has_second_chip_set else SEASON_LAST_GW
        last_chance = gw == half_end
        tc_this_week = False
        if chip is None and not tc_used[half]:
            easy_fwds = xi[(xi["position_label"] == "FWD") & (xi["difficulty"] <= 2)]
            if not easy_fwds.empty and easy_fwds["predicted_points"].max() >= fwd_threshold:
                tc_this_week = True
                captain_id = easy_fwds.sort_values("predicted_points", ascending=False).iloc[0]["element"]
            elif last_chance:
                tc_this_week = True  # don't waste the chip — force it on the best remaining option
            if tc_this_week:
                tc_used[half] = True
                chip = "Triple Captain"

        final_xi_ids = apply_auto_subs(xi, bench, gw_pool)

        captain_pts, captain_mins = real_outcome(captain_id, gw_pool)
        effective_captain = captain_id
        if captain_mins == 0:
            vice_pts, vice_mins = real_outcome(vice_id, gw_pool)
            if vice_mins > 0:
                effective_captain, captain_pts = vice_id, vice_pts

        squads_log.append({
            "gw": int(gw),
            "chip": chip or "",
            "transfers": transfers_made if isinstance(transfers_made, int) else None,
            "hits": hits,
            "starting_xi": [
                player_entry(row, gw_pool, team_names, captain_id, vice_id, effective_captain, tc_this_week)
                for _, row in xi.iterrows()
            ],
            "bench": [
                player_entry(row, gw_pool, team_names, captain_id, vice_id, effective_captain, tc_this_week)
                for _, row in bench.iterrows()
            ],
        })

        multiplier = 3 if tc_this_week else 2
        starting_points = sum(real_outcome(e, gw_pool)[0] for e in final_xi_ids)
        gw_score = starting_points + captain_pts * (multiplier - 1)

        if chip == "Bench Boost":
            bench_ids = [e for e in bench["element"] if e not in final_xi_ids]
            gw_score += sum(real_outcome(e, gw_pool)[0] for e in bench_ids)

        gw_score -= 4 * hits
        season_total += gw_score
        squads_log[-1]["gw_score"] = round(gw_score)
        squads_log[-1]["season_total"] = round(season_total)

        log.append({
            "GW": gw, "chip": chip or "", "transfers": transfers_made, "hits": hits,
            "free_transfers_available": free_transfers, "gw_score": gw_score,
            "season_total": season_total,
        })
        log_print(f"GW{gw:>2}  score={gw_score:>5.0f}  total={season_total:>6.0f}"
                  + (f"  [{chip}]" if chip else "")
                  + (f"  transfers={transfers_made} hits={hits}" if isinstance(transfers_made, int) and transfers_made else ""))

        new_purchase_prices = carry_purchase_prices(squad_ids, current_squad, gw_pool)
        current_squad = squad_resolved[["element", "name", "team", "position_label", "value"]].copy()
        current_squad["purchase_price"] = current_squad["element"].map(new_purchase_prices)
        if gw == 1 or gw in WILDCARD_GWS:
            pass  # doesn't touch banked free transfers
        else:
            used_free = min(transfers_made, free_transfers)
            free_transfers = min(5, (free_transfers - used_free) + 1)

    if not quiet:
        log_df = pd.DataFrame(log)
        log_df.to_csv(OUT_PATH, index=False)

        with SQUADS_OUT_PATH.open("w", encoding="utf-8") as f:
            json.dump({"season": SEASON, "final_score": round(season_total), "gameweeks": squads_log}, f, indent=2)

        print(f"\n=== Final season total: {season_total:.0f} points ===")
        print(f"Gameweek log saved -> {OUT_PATH}")
        print(f"Squad-by-gameweek detail saved -> {SQUADS_OUT_PATH}")

    return season_total


if __name__ == "__main__":
    simulate()
