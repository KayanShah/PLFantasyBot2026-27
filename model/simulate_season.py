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
  - Two chip sets (2025-26): Wildcard, Free Hit, Bench Boost, Triple Captain per half.

Chip timing (dynamic, all online decisions — no lookahead beyond the current
gameweek's known state):
  Wildcard: triggered the first gameweek within a per-half window where a full
  squad reoptimization beats the best available normal transfer by at least
  WC_TRIGGER_MARGIN — i.e. only when the squad has genuinely decayed enough to
  be worth resetting — falling back to the last gameweek in the window if
  never triggered, so the chip isn't wasted. Bench Boost is played the
  gameweek immediately after Wildcard fires, when the whole squad (bench
  included) is freshly optimized.
  Free Hit: triggered when >= FREE_HIT_BLANK_THRESHOLD of the current squad
  have no fixture that gameweek (a blank gameweek) — a full one-week-only
  reoptimization that reverts to the pre-Free-Hit squad next gameweek.
  Triple Captain: played on the first gameweek in each half where the best
  available captain option is a forward with an easy fixture (FDR <= 2),
  per the "best strikers, easy game" brief — falls back to the best
  available option on the last gameweek of the half if never triggered.
"""

from pathlib import Path

import numpy as np
import pandas as pd

import train_model
from optimizer import pick_captains, pick_starting_xi, select_squad

SEASON = "2025-26"
PRIOR_SEASON = "2024-25"
STARTING_BUDGET = 1000  # £100.0m, in tenths

HALF1_LAST_GW = 19
SEASON_LAST_GW = 38

# Windows within which each half's Wildcard may be triggered.
WC1_WINDOW = list(range(6, 11))   # GW6-10
WC2_WINDOW = list(range(17, 22))  # GW17-21
# A full reoptimization must beat the best normal transfer by at least this
# many (decayed, horizon-summed) points to justify resetting the squad.
WC_TRIGGER_MARGIN = 8

# Free Hit fires when at least this many of the current squad have no
# fixture that gameweek (community-standard threshold — see research.md).
FREE_HIT_BLANK_THRESHOLD = 3

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

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "season_2025-26_simulation.csv"


def build_predictions(model) -> pd.DataFrame:
    """
    Builds a predicted_points column for every 2025-26 (player, gameweek) row,
    using rolling form carried over from the end of 2024-25 for early-season
    gameweeks (so GW1 predictions reflect real known form, not zero-knowledge —
    the model itself still never trains on any 2025-26 result).
    """
    prior = train_model.load_season(PRIOR_SEASON)
    current = train_model.load_season(SEASON)
    combined = pd.concat([prior, current], ignore_index=True)

    season_order = {PRIOR_SEASON: 0, SEASON: 1}
    combined["season_order"] = combined["season"].map(season_order)
    combined = combined.sort_values(["name", "season_order", "GW"]).reset_index(drop=True)

    combined = train_model.add_rolling_features(combined, group_keys=("name",), presorted=True)
    for stat in train_model.ROLLING_STATS:
        for window in train_model.ROLLING_WINDOWS:
            col = f"{stat}_avg{window}"
            combined[col] = combined[col].fillna(0)  # rookies/new arrivals: no known history

    combined["position_label"] = (
        combined[["position_GKP", "position_DEF", "position_MID", "position_FWD"]]
        .idxmax(axis=1).str.replace("position_", "", regex=False)
    )

    rows = combined[combined["season"] == SEASON].copy()
    rows["predicted_points"] = model.predict(rows[train_model.FEATURE_COLUMNS])
    return rows


def build_horizon_scores(model, predictions: pd.DataFrame, gw: int, horizon: int) -> pd.Series:
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
        preds = pd.Series(model.predict(feat[train_model.FEATURE_COLUMNS]), index=common)
        totals = totals.add(preds * (LOOKAHEAD_DECAY ** h), fill_value=0)

    return totals


def with_horizon_points(model, predictions: pd.DataFrame, gw: int, gw_pool: pd.DataFrame) -> pd.DataFrame:
    """gw_pool with predicted_points replaced by the lookahead-summed score, for squad-construction decisions."""
    horizon_scores = build_horizon_scores(model, predictions, gw, LOOKAHEAD_GWS)
    pool = gw_pool.copy()
    pool["predicted_points"] = pool["element"].map(horizon_scores).fillna(pool["predicted_points"])
    return pool


def normal_transfer_search(
    horizon_pool: pd.DataFrame, current_squad: pd.DataFrame, budget: int, free_transfers: int
) -> tuple[int, float, pd.DataFrame]:
    """Searches 0..min(free_transfers+2, 5) transfers, judged by lookahead value minus hit cost."""
    current_ids = set(current_squad["element"])
    current_horizon = attach_this_week(current_ids, current_squad, horizon_pool)
    best_k, best_net, best_squad = 0, current_horizon["predicted_points"].sum(), current_squad
    for k in range(1, min(free_transfers + 2, 5) + 1):
        candidate = select_squad(horizon_pool, budget=budget, current_ids=current_ids, max_changes=k)
        if candidate is None:
            continue
        resolved = attach_this_week(set(candidate["element"]), current_squad, horizon_pool)
        net = resolved["predicted_points"].sum() - 4 * max(0, k - free_transfers)
        if net > best_net:
            best_k, best_net, best_squad = k, net, candidate
    return best_k, best_net, best_squad


def squad_value(current_squad: pd.DataFrame, gw_pool: pd.DataFrame) -> int:
    live_prices = gw_pool.set_index("element")["value"]
    fallback_prices = current_squad.set_index("element")["value"]
    total = 0.0
    for element in current_squad["element"]:
        total += live_prices[element] if element in live_prices.index else fallback_prices[element]
    return int(round(total))


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


def simulate(model=None, predictions: pd.DataFrame | None = None, quiet: bool = False) -> float:
    log_print = (lambda *a, **k: None) if quiet else print

    if model is None:
        log_print("Training model on 2020-21 -> 2024-25 (2025-26 never used in training)...")
        model = train_model.train_baseline_model()

    if predictions is None:
        log_print("Building week-by-week 2025-26 predictions (rolling form only, no lookahead)...")
        predictions = build_predictions(model)

    fwd_threshold = predictions.loc[predictions["position_label"] == "FWD", "predicted_points"].quantile(0.90)
    log_print(f"Triple Captain trigger threshold (90th percentile FWD prediction): {fwd_threshold:.2f}\n")

    gameweeks = sorted(predictions["GW"].unique())
    current_squad = None
    free_transfers = 1
    tc_used = {1: False, 2: False}
    wc_used = {1: False, 2: False}
    bb_used = {1: False, 2: False}
    fh_used = {1: False, 2: False}
    bb_pending_gw = None
    season_total = 0
    log = []

    for gw in gameweeks:
        gw_pool = predictions[predictions["GW"] == gw].copy()
        if gw_pool.empty:
            continue

        hits = 0
        chip = None
        horizon_pool = with_horizon_points(model, predictions, gw, gw_pool)
        half = 1 if gw <= HALF1_LAST_GW else 2

        if gw == 1:
            squad = select_squad(horizon_pool, budget=STARTING_BUDGET)
            transfers_made = 0
        else:
            budget = squad_value(current_squad, gw_pool)
            current_ids = set(current_squad["element"])
            blanked_count = len(current_ids - set(gw_pool["element"]))
            norm_k, norm_net, norm_squad = normal_transfer_search(horizon_pool, current_squad, budget, free_transfers)

            if bb_pending_gw == gw and not bb_used[half]:
                squad, transfers_made, hits = norm_squad, norm_k, max(0, norm_k - free_transfers)
                chip = "Bench Boost"
                bb_used[half] = True
                bb_pending_gw = None
            elif blanked_count >= FREE_HIT_BLANK_THRESHOLD and not fh_used[half]:
                fh_squad = select_squad(gw_pool, budget=budget)
                if fh_squad is not None:
                    squad, transfers_made, hits = fh_squad, "FH", 0
                    chip = "Free Hit"
                    fh_used[half] = True
                else:
                    squad, transfers_made, hits = norm_squad, norm_k, max(0, norm_k - free_transfers)
            else:
                window = WC1_WINDOW if half == 1 else WC2_WINDOW
                full_reopt = select_squad(horizon_pool, budget=budget) if not wc_used[half] and gw in window else None
                if full_reopt is not None:
                    full_resolved = attach_this_week(set(full_reopt["element"]), current_squad, horizon_pool)
                    full_value = full_resolved["predicted_points"].sum()
                    is_last_in_window = gw == window[-1]
                    if (full_value - norm_net) >= WC_TRIGGER_MARGIN or is_last_in_window:
                        squad, transfers_made, hits = full_reopt, None, 0
                        chip = "Wildcard"
                        wc_used[half] = True
                        bb_pending_gw = gw + 1
                    else:
                        squad, transfers_made, hits = norm_squad, norm_k, max(0, norm_k - free_transfers)
                else:
                    squad, transfers_made, hits = norm_squad, norm_k, max(0, norm_k - free_transfers)

        squad_ids = set(squad["element"])
        squad_resolved = attach_this_week(squad_ids, current_squad if current_squad is not None else squad, gw_pool)
        xi, bench = pick_starting_xi(squad_resolved)
        captain_id, vice_id = pick_captains(xi)

        # Triple Captain: online decision using only this gameweek's predictions.
        last_chance = (half == 1 and gw == HALF1_LAST_GW) or (half == 2 and gw == SEASON_LAST_GW)
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

        multiplier = 3 if tc_this_week else 2
        starting_points = sum(real_outcome(e, gw_pool)[0] for e in final_xi_ids)
        gw_score = starting_points + captain_pts * (multiplier - 1)

        if chip == "Bench Boost":
            bench_ids = [e for e in bench["element"] if e not in final_xi_ids]
            gw_score += sum(real_outcome(e, gw_pool)[0] for e in bench_ids)

        gw_score -= 4 * hits
        season_total += gw_score

        log.append({
            "GW": gw, "chip": chip or "", "transfers": transfers_made, "hits": hits,
            "free_transfers_available": free_transfers, "gw_score": gw_score,
            "season_total": season_total,
        })
        log_print(f"GW{gw:>2}  score={gw_score:>5.0f}  total={season_total:>6.0f}"
                  + (f"  [{chip}]" if chip else "")
                  + (f"  transfers={transfers_made} hits={hits}" if isinstance(transfers_made, int) and transfers_made else ""))

        if chip != "Free Hit":
            # Free Hit's squad is temporary — reverts to the pre-Free-Hit squad next gameweek.
            current_squad = squad_resolved[["element", "name", "team", "position_label", "value"]].copy()

        if gw == 1 or chip in ("Wildcard", "Free Hit"):
            pass  # doesn't touch banked free transfers
        else:
            used_free = min(transfers_made, free_transfers)
            free_transfers = min(5, (free_transfers - used_free) + 1)

    if not quiet:
        log_df = pd.DataFrame(log)
        log_df.to_csv(OUT_PATH, index=False)
        print(f"\n=== Final season total: {season_total:.0f} points ===")
        print(f"Gameweek log saved -> {OUT_PATH}")

    return season_total


if __name__ == "__main__":
    simulate()
