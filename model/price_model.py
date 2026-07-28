"""
Predicts how a player's price will move over the next gameweek.

FPL prices follow net transfer traffic, not performance directly
(FantasyRules.md section 7), and the relationship is visible and lagged in the
historical data. Salah's net transfers ran -652k, -708k, -715k, -845k from
GW6 of 2025-26 and his price fell 14.5 -> 14.2 across GW8-10.

This is deliberately *not* wired into what the bot values. Squad choice still
maximises predicted points; a predicted price move only breaks ties between
options the points model rates as effectively equal. `plan.md` traced this
pipeline's largest instability to budget path-dependency, so letting price
drive selection outright would amplify exactly the wrong thing.

The columns this needs (`transfers_balance`, `selected`) are dropped by
train_model.load_season, so the raw per-gameweek file is re-read here.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"

PRICE_FEATURES = [
    "transfers_balance", "transfers_balance_avg3", "transfers_in", "transfers_out",
    "selected", "value", "net_transfer_share",
]


def load_transfer_flow(season: str) -> pd.DataFrame:
    """Per (player, gameweek) price and transfer traffic, double-gameweeks collapsed."""
    path = DATA_DIR / season / "merged_gw.csv"
    df = pd.read_csv(path, encoding="utf-8", encoding_errors="ignore")
    columns = ["element", "GW", "value", "transfers_balance", "transfers_in",
               "transfers_out", "selected"]
    df = df[[c for c in columns if c in df.columns]].copy()
    df = df[df["GW"].notna()]

    aggregation = {"value": "mean", "selected": "mean", "transfers_balance": "sum",
                   "transfers_in": "sum", "transfers_out": "sum"}
    df = df.groupby(["element", "GW"], as_index=False).agg(
        {k: v for k, v in aggregation.items() if k in df.columns}
    )
    df["season"] = season
    return df.sort_values(["element", "GW"]).reset_index(drop=True)


def add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby("element", group_keys=False)
    df["transfers_balance_avg3"] = grouped["transfers_balance"].transform(
        lambda s: s.rolling(3, min_periods=1).mean()
    )
    # Traffic only moves a price relative to how many managers already own the
    # player -- 100k transfers out of a 5% owned player is a very different
    # signal from 100k out of a 50% owned one.
    df["net_transfer_share"] = df["transfers_balance"] / df["selected"].clip(lower=1)
    # The target: how this player's price moves into the next gameweek. Every
    # feature above is known at that gameweek's deadline, so there is no leak.
    df["price_delta"] = grouped["value"].transform(lambda s: s.shift(-1) - s)
    return df


def train_price_model(seasons: list[str], seed: int = 42) -> GradientBoostingRegressor:
    frames = [add_price_features(load_transfer_flow(s)) for s in seasons]
    train = pd.concat(frames, ignore_index=True).dropna(subset=["price_delta"])
    model = GradientBoostingRegressor(
        n_estimators=150, max_depth=3, learning_rate=0.05, random_state=seed
    )
    model.fit(train[PRICE_FEATURES], train["price_delta"])
    return model


def predicted_price_delta(model, season: str) -> pd.DataFrame:
    """(element, GW) -> predicted price move into the following gameweek, in tenths."""
    rows = add_price_features(load_transfer_flow(season))
    rows["predicted_price_delta"] = model.predict(rows[PRICE_FEATURES])
    return rows[["element", "GW", "predicted_price_delta"]]


def main() -> None:
    train_seasons = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    test_season = "2025-26"

    model = train_price_model(train_seasons)
    rows = add_price_features(load_transfer_flow(test_season)).dropna(subset=["price_delta"])
    predicted = model.predict(rows[PRICE_FEATURES])
    actual = rows["price_delta"].to_numpy()

    print(f"Trained on {', '.join(train_seasons)}; tested on {test_season}")
    print(f"  rows: {len(rows)}   price changes: {(actual != 0).mean():.1%} of rows")
    print(f"  MAE : {np.abs(predicted - actual).mean():.4f} tenths")
    print(f"  MAE of always-predicting-no-change: {np.abs(actual).mean():.4f}")
    print(f"  correlation: {np.corrcoef(actual, predicted)[0, 1]:.3f}")

    # Direction is what the tie-break actually uses, so score that directly.
    moved = actual != 0
    agree = np.sign(predicted[moved]) == np.sign(actual[moved])
    print(f"  direction correct on rows that moved: {agree.mean():.1%} of {moved.sum()}")

    print("\n  feature importance:")
    for name, importance in sorted(
        zip(PRICE_FEATURES, model.feature_importances_), key=lambda x: -x[1]
    ):
        print(f"    {name:<24} {importance:.3f}")


if __name__ == "__main__":
    main()
