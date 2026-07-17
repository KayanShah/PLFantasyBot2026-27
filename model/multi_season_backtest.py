"""
Runs the full train + simulate pipeline against multiple past seasons, each
trained only on seasons strictly *before* it (no leakage — a 2023-24 test
never trains on 2023-24 or later), and compares the result to that season's
real average-manager total.

This is what actually validates the bot's approach: a single season's
backtest is one noisy data point (see plan.md Phase 4), but a result that
holds up across several independent seasons is a real signal.
"""

import train_model
import simulate_season

# For each test season, train only on seasons strictly before it.
CONFIGS = [
    {"test": "2023-24", "train": ["2020-21", "2021-22", "2022-23"], "prior": "2022-23"},
    {"test": "2024-25", "train": ["2020-21", "2021-22", "2022-23", "2023-24"], "prior": "2023-24"},
    {"test": "2025-26", "train": ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"], "prior": "2024-25"},
]

# Real average-manager totals (sum of each gameweek's average_entry_score).
# 2025-26 comes from data/fpl.db (current live season). Earlier seasons are
# no longer served by the live API once a season ends, so these are pulled
# from Wayback Machine snapshots of bootstrap-static taken at each season's
# end — see plan.md Phase 6 for the exact snapshot URLs used.
AVERAGE_MANAGER = {
    "2022-23": 2026,
    "2023-24": 2003,
    "2024-25": 2008,
    "2025-26": 1895,
}


def main() -> None:
    results = []
    for cfg in CONFIGS:
        train_model.TRAIN_SEASONS = cfg["train"]
        train_model.TEST_SEASON = cfg["test"]
        simulate_season.SEASON = cfg["test"]
        simulate_season.PRIOR_SEASON = cfg["prior"]

        print(f"\n=== Testing on {cfg['test']} (trained on {cfg['train']}) ===")
        model = train_model.train_baseline_model()
        predictions = simulate_season.build_predictions(model)
        total = simulate_season.simulate(model=model, predictions=predictions, quiet=True)
        avg = AVERAGE_MANAGER[cfg["test"]]
        print(f"{cfg['test']}: bot={total:.0f}  avg_manager={avg}  diff={total - avg:+.0f}")
        results.append((cfg["test"], total, avg))

    print("\n\n=== Summary ===")
    print(f"{'Season':<10} {'Bot':>6} {'Avg Manager':>12} {'Diff':>8}")
    for season, total, avg in results:
        print(f"{season:<10} {total:>6.0f} {avg:>12} {total - avg:>+8.0f}")


if __name__ == "__main__":
    main()
