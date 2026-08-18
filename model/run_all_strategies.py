"""
Runs every named strategy in strategies.STRATEGIES against a season backtest
(default 2025-26, the season the Balanced build's own validation already
uses) and writes one squads JSON per strategy plus a manifest the website
reads to build the strategy switcher.

Model training, feature building and price-model fitting happen once and are
shared across all four strategies -- only the squad-construction/transfer
policy differs between them (see strategies.py), so there's no reason to
retrain four times what only needs training once. The Differential strategy's
ownership tilt is applied as a cheap post-hoc reweight of the shared
predictions, not a different model.

Usage:
    python model/run_all_strategies.py                # 2025-26 backtest (default)
    python model/run_all_strategies.py --season 2024-25 --prior 2023-24
"""

import argparse
import json
from pathlib import Path

import price_model
import train_model
from simulate_season import (
    ENSEMBLE_EXTRA_SEEDS, apply_differential_tilt, build_predictions, simulate,
)
from strategies import STRATEGIES, ordered

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main(season: str = "2025-26", prior_season: str = "2024-25") -> None:
    print(f"Training model on {train_model.TRAIN_SEASONS[0]} -> {train_model.TRAIN_SEASONS[-1]}...")
    model = train_model.train_baseline_model()

    print(f"Training {1 + len(ENSEMBLE_EXTRA_SEEDS)}-model ensemble for GW1/Wildcard squad construction...")
    ensemble_models = [model] + [train_model.train_baseline_model(seed=s) for s in ENSEMBLE_EXTRA_SEEDS]

    print(f"Building week-by-week {season} predictions (shared across all strategies)...")
    base_predictions = build_predictions([model], season, prior_season)
    base_predictions_ensemble = build_predictions(ensemble_models, season, prior_season)

    print("Training price model for transfer tie-breaks...")
    price_deltas = price_model.predicted_price_delta(
        price_model.train_price_model(train_model.TRAIN_SEASONS), season
    )

    manifest = {"season": season, "strategies": []}

    for key, cfg in ordered():
        print(f"\n=== {cfg['label']} ({key}) ===")
        squads_path = DATA_DIR / f"season_{season}_squads_{key}.json"
        sim_path = DATA_DIR / f"season_{season}_simulation_{key}.csv"

        predictions = apply_differential_tilt(base_predictions, cfg["differential_weight"])
        predictions_ensemble = apply_differential_tilt(base_predictions_ensemble, cfg["differential_weight"])

        final_score = simulate(
            model=model, predictions=predictions,
            ensemble_models=ensemble_models, predictions_ensemble=predictions_ensemble,
            price_deltas=price_deltas,
            season=season, prior_season=prior_season,
            transfer_margin=cfg["transfer_margin"],
            max_hits_per_gw=cfg["max_hits_per_gw"],
            lookahead_gws=cfg["lookahead_gws"],
            differential_weight=0.0,  # already applied above -- avoid tilting twice
            out_path=sim_path, squads_out_path=squads_path,
        )

        manifest["strategies"].append({
            "key": key, "label": cfg["label"], "short": cfg["short"],
            "description": cfg["description"], "risk": cfg["risk"],
            "risk_tier": cfg["risk_tier"], "order": cfg["order"],
            "final_score": round(final_score), "squads_file": squads_path.name,
        })

    manifest_path = DATA_DIR / f"strategies_manifest_{season}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nWrote manifest -> {manifest_path}")
    print("\n=== Summary ===")
    for entry in sorted(manifest["strategies"], key=lambda e: -e["final_score"]):
        print(f"  {entry['label']:<14} {entry['final_score']:>5} pts  ({entry['risk']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--prior", default="2024-25")
    args = parser.parse_args()
    main(args.season, args.prior)
