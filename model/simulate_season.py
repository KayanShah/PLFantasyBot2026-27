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
  - Two chip sets (2025-26): Wildcard, Bench Boost, Triple Captain per half.
    (Free Hit is not simulated — kept out of scope for simplicity.)
  - Captain 2x, Triple Captain 3x, auto-subs for starters who didn't play.

Chip schedule (fixed heuristic, avoids GW clashes):
  Wildcard 1  -> GW8    Bench Boost 1 -> GW9
  Wildcard 2  -> GW20   Bench Boost 2 -> GW21
  Triple Captain: played on the first gameweek in each half where the best
  available captain option is a forward with an easy fixture (FDR <= 2),
  per the "best strikers, easy game" brief — falls back to the best
  available option on the last gameweek of the half if never triggered.
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

WILDCARD_GWS = {8, 20}
BENCH_BOOST_GWS = {9, 21}
CHIP_GWS = WILDCARD_GWS | BENCH_BOOST_GWS
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

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "season_2025-26_simulation.csv"
SQUADS_OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "season_2025-26_squads.json"
TEAMS_PATH = Path(__file__).resolve().parent.parent / "data" / "historical" / SEASON / "teams.csv"


def load_team_names() -> dict[int, str]:
    teams = pd.read_csv(TEAMS_PATH, encoding="utf-8", encoding_errors="ignore")
    return dict(zip(teams["id"], teams["short_name"]))


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
    grouped = combined.groupby("name", group_keys=False)

    for stat in train_model.ROLLING_STATS:
        for window in train_model.ROLLING_WINDOWS:
            col = f"{stat}_avg{window}"
            combined[col] = grouped[stat].transform(
                lambda s, w=window: s.shift(1).rolling(w, min_periods=1).mean()
            )
            combined[col] = combined[col].fillna(0)  # rookies/new arrivals: no known history

    combined = pd.get_dummies(combined, columns=["position"], prefix="position")
    for col in ["position_DEF", "position_FWD", "position_GKP", "position_MID"]:
        if col not in combined.columns:
            combined[col] = 0
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
        horizon_pool = with_horizon_points(model, predictions, gw, gw_pool)

        if gw == 1:
            squad = select_squad(horizon_pool, budget=STARTING_BUDGET)
            transfers_made = 0
        elif gw in WILDCARD_GWS:
            budget = squad_value(current_squad, gw_pool)
            squad = select_squad(horizon_pool, budget=budget)
            transfers_made = None
            chip = "Wildcard"
        else:
            budget = squad_value(current_squad, gw_pool)
            current_ids = set(current_squad["element"])
            current_horizon = attach_this_week(current_ids, current_squad, horizon_pool)
            best_k, best_net, best_squad = 0, current_horizon["predicted_points"].sum(), current_squad
            for k in range(1, min(free_transfers + 2, 5) + 1):
                candidate = select_squad(horizon_pool, budget=budget, current_ids=current_ids, max_changes=k)
                if candidate is None:
                    continue
                # Judge the transfer by its lookahead value (worth a hit only if the gain
                # over the next LOOKAHEAD_GWS gameweeks outweighs the -4), not just this week.
                resolved = attach_this_week(set(candidate["element"]), current_squad, horizon_pool)
                net = resolved["predicted_points"].sum() - 4 * max(0, k - free_transfers)
                if net > best_net:
                    best_k, best_net, best_squad = k, net, candidate
            squad = best_squad
            transfers_made = best_k
            hits = max(0, best_k - free_transfers)
            if gw in BENCH_BOOST_GWS:
                chip = "Bench Boost"

        squad_ids = set(squad["element"])
        squad_resolved = attach_this_week(squad_ids, current_squad if current_squad is not None else squad, gw_pool)
        xi, bench = pick_starting_xi(squad_resolved)
        captain_id, vice_id = pick_captains(xi)

        # Triple Captain: online decision using only this gameweek's predictions.
        half = 1 if gw <= HALF1_LAST_GW else 2
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

        current_squad = squad_resolved[["element", "name", "team", "position_label", "value"]].copy()
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
