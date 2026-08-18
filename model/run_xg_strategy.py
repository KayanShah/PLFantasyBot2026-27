"""
A separate, isolated strategy that trains with xG/xA features enabled, to
test plan.md's earlier finding a second way: an isolated feature-importance
check found xG never ranks in the model's top 6 features and is a
wash-to-regression on the squad-relevant top-150 split. That was a check on
the model in isolation -- this runs the same idea all the way through an
actual backtested season, using the identical policy knobs as Balanced (same
hit ceiling, same 5-week lookahead), so the only variable being tested is
"does the prediction model do better with xG features on."

Deliberately its own script, not a fifth entry in strategies.py's shared
run_all_strategies.py: train_model.enable_xg_features() mutates
COMMON_COLUMNS/SUM_COLUMNS/ROLLING_STATS/FEATURE_COLUMNS in place, which
would silently change the feature set every other strategy trains on if run
in the same process. Running this as its own process means that mutation
dies with the process and never touches the other four strategies.

Training is restricted to 2022-23 GW16 onward: FPL's xG columns don't exist
before 2022-23 at all, and are present-but-always-0.0 for 2022-23 GW1-15
(load_season() already filters that stretch out once xG features are on,
per train_model.XG_FIRST_SEASON/XG_FIRST_GW).

Writes into the same manifest run_all_strategies.py writes, so
website/build_site.py picks this up alongside the other four with no changes
needed -- ordered last (order=4), since it's an explicitly experimental,
already-suspected-not-to-help variant, not a recommended pick.

Usage:
    python model/run_xg_strategy.py                # 2025-26 backtest (default)
"""

import argparse
import json
from pathlib import Path

import price_model
import train_model
from simulate_season import ENSEMBLE_EXTRA_SEEDS, build_predictions, simulate
from strategies import STRATEGIES

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

XG_TRAIN_SEASONS = ["2022-23", "2023-24", "2024-25"]


def main(season: str = "2025-26", prior_season: str = "2024-25") -> None:
    train_model.enable_xg_features()
    train_model.TRAIN_SEASONS = XG_TRAIN_SEASONS

    print(f"Training model on {XG_TRAIN_SEASONS[0]} -> {XG_TRAIN_SEASONS[-1]} (xG/xA features ON)...")
    model = train_model.train_baseline_model()

    print(f"Training {1 + len(ENSEMBLE_EXTRA_SEEDS)}-model ensemble for GW1/Wildcard squad construction...")
    ensemble_models = [model] + [train_model.train_baseline_model(seed=s) for s in ENSEMBLE_EXTRA_SEEDS]

    print(f"Building week-by-week {season} predictions...")
    predictions = build_predictions([model], season, prior_season)
    predictions_ensemble = build_predictions(ensemble_models, season, prior_season)

    print("Training price model for transfer tie-breaks...")
    price_deltas = price_model.predicted_price_delta(
        price_model.train_price_model(XG_TRAIN_SEASONS), season
    )

    # Same policy as Balanced -- isolating the one variable under test (xG
    # on/off) instead of also varying hit ceiling/lookahead at the same time.
    cfg = STRATEGIES["balanced"]
    squads_path = DATA_DIR / f"season_{season}_squads_xg_experimental.json"
    sim_path = DATA_DIR / f"season_{season}_simulation_xg_experimental.csv"

    final_score = simulate(
        model=model, predictions=predictions,
        ensemble_models=ensemble_models, predictions_ensemble=predictions_ensemble,
        price_deltas=price_deltas,
        season=season, prior_season=prior_season,
        transfer_margin=cfg["transfer_margin"], max_hits_per_gw=cfg["max_hits_per_gw"],
        lookahead_gws=cfg["lookahead_gws"], differential_weight=0.0,
        out_path=sim_path, squads_out_path=squads_path,
    )

    entry = {
        "key": "xg_experimental", "label": "xG Experimental",
        "short": "Balanced's policy, xG/xA features on.",
        "description": (
            "Identical policy to Balanced (same hit ceiling, same 5-week lookahead) "
            "-- the only difference is the prediction model itself trains on "
            "expected-goals/assists features (2022-23 GW16 onward only, since xG "
            "isn't available before that). An earlier isolated-importance check "
            "found xG never ranks in the model's top 6 features and is a "
            "wash-to-regression on the squad-relevant top-150 split; this asks the "
            "same question a second way, through an actual season simulation "
            "instead of a feature-importance ranking."
        ),
        "risk": "Experimental", "risk_tier": "medium", "order": 4,
        "final_score": round(final_score), "squads_file": squads_path.name,
    }

    manifest_path = DATA_DIR / f"strategies_manifest_{season}.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {"season": season, "strategies": []}
    )
    manifest["strategies"] = [s for s in manifest["strategies"] if s["key"] != "xg_experimental"] + [entry]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    balanced_score = next(
        (s["final_score"] for s in manifest["strategies"] if s["key"] == "balanced"), None
    )
    print(f"\n=== xG Experimental: {round(final_score)} pts (Balanced: {balanced_score}) ===")
    print(f"Wrote {squads_path}")
    print(f"Updated manifest -> {manifest_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--prior", default="2024-25")
    args = parser.parse_args()
    main(args.season, args.prior)
