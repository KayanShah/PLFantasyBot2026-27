"""
Trains a player-points prediction model on past seasons (2020-21 to 2024-25)
and backtests it against the 2025-26 season, which the model never sees
during training.

For each (player, gameweek) row, features are built only from *prior*
gameweeks' rolling stats, so the model can never see the outcome it's
predicting (no leakage) — this mirrors how it would actually be used
before a real, not-yet-played gameweek.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
TRAIN_SEASONS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
TEST_SEASON = "2025-26"

COMMON_COLUMNS = [
    "name", "position", "team", "opponent_team", "element", "GW", "was_home", "value", "fixture",
    "minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_missed", "penalties_saved", "yellow_cards",
    "red_cards", "saves", "bonus", "bps", "influence", "creativity",
    "threat", "ict_index", "total_points",
]

# Per-match stats that must be summed (not averaged/first-taken) when a player
# has two rows in the same gameweek because their club played twice (a "double
# gameweek") — FPL adds both matches' points together for that gameweek.
SUM_COLUMNS = [
    "minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_missed", "penalties_saved", "yellow_cards",
    "red_cards", "saves", "bonus", "bps", "influence", "creativity",
    "threat", "ict_index", "total_points",
]

ROLLING_STATS = [
    "total_points", "minutes", "goals_scored", "assists", "bps",
    "ict_index", "influence", "creativity", "threat", "clean_sheets",
]
ROLLING_WINDOWS = [3, 5]

# Expected-goals columns. These are already in merged_gw.csv -- COMMON_COLUMNS
# simply never selected them, so they were dropped before anything downstream
# could see them. Off by default: enable_xg_features() switches them on, so a
# run that doesn't call it is byte-identical to the shipped pipeline.
#
# expected_goals_conceded is per-player but constant across a club's outfield
# players in a given match, so it doubles as a measure of how much a team
# leaks -- which is what `difficulty` currently gestures at with a hand-set
# 1-5 integer.
XG_STATS = [
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded",
]

# FPL only began publishing xG partway through 2022-23. Before this point the
# columns exist and are 0.0 rather than blank, which is worse than missing --
# a rolling average would read fifteen gameweeks of fabricated zeros as real.
# Measured, not assumed: max expected_goals across all played rows is exactly
# 0.0 for every 2022-23 gameweek up to GW15, and non-zero from GW16 on.
XG_FIRST_SEASON = "2022-23"
XG_FIRST_GW = 16

FEATURE_COLUMNS = (
    [f"{stat}_avg{w}" for stat in ROLLING_STATS for w in ROLLING_WINDOWS]
    + ["value", "was_home", "difficulty", "fixture_count",
       "position_DEF", "position_FWD", "position_GKP", "position_MID"]
)


def enable_xg_features() -> None:
    """
    Adds the xG columns to the rolling-feature set, in place, so existing call
    sites that read train_model.FEATURE_COLUMNS pick them up without change.

    Callers must also restrict training to seasons where xG actually exists
    (2022-23 GW16 onward) -- GradientBoostingRegressor cannot take NaN, and
    filling the gap with zeros is precisely the corruption this guards against.
    """
    for stat in XG_STATS:
        if stat not in COMMON_COLUMNS:
            COMMON_COLUMNS.append(stat)
        if stat not in SUM_COLUMNS:
            SUM_COLUMNS.append(stat)
        if stat not in ROLLING_STATS:
            ROLLING_STATS.append(stat)
    FEATURE_COLUMNS[:] = (
        [f"{stat}_avg{w}" for stat in ROLLING_STATS for w in ROLLING_WINDOWS]
        + ["value", "was_home", "difficulty", "fixture_count",
           "position_DEF", "position_FWD", "position_GKP", "position_MID"]
    )


def load_player_codes(season: str) -> pd.DataFrame:
    """
    Maps that season's `element` (FPL's per-season player index, re-numbered
    every season) to `code` (FPL's actual stable player identifier, constant
    for a player across every season of their career). Needed to carry a
    player's rolling form across a season boundary without joining on their
    name string, which can change format season to season (nicknames,
    added/dropped middle names, transliteration of accented characters).
    """
    path = DATA_DIR / season / "players_raw.csv"
    players = pd.read_csv(path, encoding="utf-8", encoding_errors="ignore")
    return players[["id", "code"]].rename(columns={"id": "element", "code": "player_code"})


def load_fixture_difficulty(season: str) -> pd.DataFrame:
    path = DATA_DIR / season / "fixtures.csv"
    fixtures = pd.read_csv(path, encoding="utf-8", encoding_errors="ignore")
    return fixtures[["id", "team_h_difficulty", "team_a_difficulty"]].rename(columns={"id": "fixture"})


def load_season(season: str) -> pd.DataFrame:
    path = DATA_DIR / season / "merged_gw.csv"
    df = pd.read_csv(path, encoding="utf-8", encoding_errors="ignore")
    # One row per (player, gameweek, fixture) is the real grain. The 2025-26 file
    # ships a handful of byte-identical duplicates -- 10 rows, all one player,
    # spread across GW1-9. Left in they do two kinds of damage: fixture_count
    # reads 2 and invents a double gameweek that never happened (the fixture list
    # puts 2025-26's only doubles in GW26, GW33 and GW36), and the sum below
    # double-counts that player's points and minutes.
    if {"element", "GW", "fixture"} <= set(df.columns):
        df = df.drop_duplicates(subset=["element", "GW", "fixture"], keep="first")
    df = df[[c for c in COMMON_COLUMNS if c in df.columns]].copy()
    # Drop the stretch of 2022-23 where the xG columns are present but always
    # zero (see XG_FIRST_GW). Only bites when xG features are enabled -- without
    # them the columns aren't selected above, so this is a no-op.
    if season == XG_FIRST_SEASON and all(s in df.columns for s in XG_STATS):
        df = df[df["GW"] >= XG_FIRST_GW]
    df["season"] = season
    df["position"] = df["position"].replace({"GK": "GKP"})
    df["was_home"] = df["was_home"].astype(bool).astype(int)

    fixtures = load_fixture_difficulty(season)
    df = df.merge(fixtures, on="fixture", how="left")
    # The difficulty of the opponent the player's own team is facing.
    df["difficulty"] = np.where(df["was_home"] == 1, df["team_h_difficulty"], df["team_a_difficulty"])
    df["difficulty"] = df["difficulty"].fillna(df["difficulty"].mean())
    df = df.drop(columns=["team_h_difficulty", "team_a_difficulty", "fixture"])

    # Collapse double-gameweek rows (same player, same GW, two fixtures) into one.
    # Summing fixture_count is what survives the collapse to tell the model a
    # double happened: the points columns get summed across both matches, but
    # difficulty is averaged and was_home taken from the first, so without this
    # a double is indistinguishable from a single against an average opponent.
    # Doubles score roughly twice as much (2.37 vs 1.16 mean points in 2025-26).
    df["fixture_count"] = 1
    agg = {col: "sum" for col in SUM_COLUMNS if col in df.columns}
    agg.update({"name": "first", "position": "first", "team": "first",
                "opponent_team": "first", "was_home": "first", "value": "mean",
                "difficulty": "mean", "fixture_count": "sum"})
    df = df.groupby(["season", "element", "GW"], as_index=False).agg(agg)

    return df


def add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["season", "element", "GW"]).reset_index(drop=True)
    grouped = df.groupby(["season", "element"], group_keys=False)

    for stat in ROLLING_STATS:
        for window in ROLLING_WINDOWS:
            # shift(1) excludes the current (target) gameweek from its own rolling average.
            df[f"{stat}_avg{window}"] = grouped[stat].transform(
                lambda s, w=window: s.shift(1).rolling(w, min_periods=1).mean()
            )

    df = pd.get_dummies(df, columns=["position"], prefix="position")
    for col in ["position_DEF", "position_FWD", "position_GKP", "position_MID"]:
        if col not in df.columns:
            df[col] = 0

    return df


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = add_rolling_features(df)
    # Drop rows with no rolling history yet (each player's first appearance in a season).
    df = df.dropna(subset=[f"total_points_avg{ROLLING_WINDOWS[0]}"])
    return df


def blank_gameweek_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """
    A row per gameweek a player's club didn't play, from the gameweek that
    player enters the league onward.

    The source data simply omits these, which makes a blank indistinguishable
    from a player who left, and makes any per-player season total silently
    incomparable -- Haaland's 2025-26 total covers 36 gameweeks, not 38,
    because City blanked in GW31 and GW34. They carry fixture_count 0 and
    score nothing.

    Kept out of the frame the optimizer sees: a zero-point player with no
    fixture must never be selectable as cheap bench filler.
    """
    club_played = set(zip(rows["team"], rows["GW"]))
    all_gws = range(int(rows["GW"].min()), int(rows["GW"].max()) + 1)
    blanks = []

    for _, group in rows.groupby("element", sort=False):
        template = group.iloc[-1]
        present = set(group["GW"])
        for gw in all_gws:
            if gw < group["GW"].min() or gw in present:
                continue
            # The club played, so this player just has no row that week (sold,
            # unregistered). Not a blank gameweek.
            if (template["team"], gw) in club_played:
                continue
            blank = template.copy()
            blank["GW"] = gw
            blank["fixture_count"] = 0
            blank["opponent_team"] = pd.NA
            for stat in SUM_COLUMNS:
                if stat in blank.index:
                    blank[stat] = 0
            blanks.append(blank)

    return pd.DataFrame(blanks) if blanks else pd.DataFrame(columns=rows.columns)


def build_season_features(
    season: str, prior_season: str, extra_rows: pd.DataFrame | None = None,
    include_blanks: bool = False,
) -> pd.DataFrame:
    """
    Feature table for `season` that keeps every row, unlike prepare(), which
    drops each player's first appearance because shift(1) leaves it with no
    rolling history.

    Two things close that gap: rolling form carries over from `prior_season`
    on the stable player `code` (not `element`, re-numbered every season, nor
    `name`, whose format can change season to season — see load_player_codes),
    and a player with no history anywhere falls back to their position's
    average rather than 0, which would read to the model as "never plays" —
    an input it never trained on, since prepare() drops exactly those rows.

    `extra_rows` appends gameweeks that have not been played yet, which the
    live pipeline needs so there is something to predict against before a
    fixture happens. Their stat columns must be NaN rather than 0 so a rolling
    window skips them, instead of counting an unplayed week as a scoreless one.
    """
    frames = [load_season(prior_season), load_season(season)]
    if extra_rows is not None and not extra_rows.empty:
        frames.append(extra_rows)
    combined = pd.concat(frames, ignore_index=True)

    codes = pd.concat([
        load_player_codes(prior_season).assign(season=prior_season),
        load_player_codes(season).assign(season=season),
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

    combined["season_order"] = combined["season"].map({prior_season: 0, season: 1})
    combined = combined.sort_values(["player_code", "season_order", "GW"]).reset_index(drop=True)
    grouped = combined.groupby("player_code", group_keys=False)

    for stat in ROLLING_STATS:
        for window in ROLLING_WINDOWS:
            col = f"{stat}_avg{window}"
            combined[col] = grouped[stat].transform(
                lambda s, w=window: s.shift(1).rolling(w, min_periods=1).mean()
            )
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

    rows = combined[combined["season"] == season].copy()
    if include_blanks:
        rows = pd.concat([rows, blank_gameweek_rows(rows)], ignore_index=True)
        rows = rows.sort_values(["player_code", "GW"]).reset_index(drop=True)
    return rows


def evaluate(y_true: np.ndarray, y_pred: np.ndarray, label: str) -> None:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = mean_squared_error(y_true, y_pred) ** 0.5
    corr = np.corrcoef(y_true, y_pred)[0, 1]
    print(f"{label}: MAE={mae:.3f}  RMSE={rmse:.3f}  correlation={corr:.3f}  n={len(y_true)}")


def train_baseline_model(seed: int = 42) -> GradientBoostingRegressor:
    """Fits the model on TRAIN_SEASONS only. Never sees TEST_SEASON (2025-26) data."""
    train_df = pd.concat([load_season(s) for s in TRAIN_SEASONS], ignore_index=True)
    train_df = prepare(train_df)
    model = GradientBoostingRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=seed
    )
    model.fit(train_df[FEATURE_COLUMNS], train_df["total_points"])
    return model


def main() -> None:
    print("Loading training seasons:", ", ".join(TRAIN_SEASONS))
    train_df = pd.concat([load_season(s) for s in TRAIN_SEASONS], ignore_index=True)
    train_df = prepare(train_df)

    print(f"Loading held-out test season: {TEST_SEASON}")
    test_df = load_season(TEST_SEASON)
    test_df = prepare(test_df)

    X_train, y_train = train_df[FEATURE_COLUMNS], train_df["total_points"]
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df["total_points"]

    print(f"\nTraining rows: {len(X_train)}  |  Test (2025-26) rows: {len(X_test)}\n")

    model = GradientBoostingRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
    )
    model.fit(X_train, y_train)

    # Baselines to contextualize the model's accuracy.
    naive_pred = X_test["total_points_avg5"]  # "predict last-5-game average"
    mean_pred = np.full(len(y_test), y_train.mean())  # "predict the training-set average"
    model_pred = model.predict(X_test)

    print("--- Backtest on 2025-26 (never seen during training) ---")
    evaluate(y_test.values, mean_pred, "Mean baseline      ")
    evaluate(y_test.values, naive_pred.values, "Last-5-avg baseline")
    evaluate(y_test.values, model_pred, "Gradient-boosted model")

    print("\n--- Feature importance (top 10) ---")
    importances = sorted(
        zip(FEATURE_COLUMNS, model.feature_importances_), key=lambda x: -x[1]
    )
    for name, importance in importances[:10]:
        print(f"  {name:<20} {importance:.3f}")

    # Built from build_season_features, not the metrics table above. prepare()
    # drops every player's first appearance, so a CSV built from it silently
    # omits all of GW1 plus any later debut, and blank gameweeks are missing
    # outright -- either alone makes a per-player season total wrong.
    out_path = DATA_DIR.parent / f"backtest_{TEST_SEASON}_predictions.csv"
    complete = build_season_features(TEST_SEASON, TRAIN_SEASONS[-1], include_blanks=True)
    complete["predicted_points"] = model.predict(complete[FEATURE_COLUMNS])
    # No fixture, no points. Predicting from a blank row's carried-forward
    # features would invent a score for a match that never gets played.
    complete.loc[complete["fixture_count"] == 0, "predicted_points"] = 0.0
    complete["total_season_predicted_points"] = (
        complete.groupby("element")["predicted_points"].transform("sum")
    )
    complete[[
        "season", "name", "GW", "fixture_count", "total_points",
        "predicted_points", "total_season_predicted_points",
    ]].to_csv(out_path, index=False)

    blanks = int((complete["fixture_count"] == 0).sum())
    print(f"\nPer-gameweek predictions saved -> {out_path}")
    print(f"  {len(complete)} rows, every gameweek of {TEST_SEASON} for every player,")
    print(f"  including {blanks} blank-gameweek rows scoring 0.")
    print(f"  Metrics above cover the {len(test_df)} rows with within-season rolling history.")


if __name__ == "__main__":
    main()
