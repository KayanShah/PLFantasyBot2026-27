"""
Downloads per-gameweek player data for past seasons from the vaastav
Fantasy-Premier-League historical dataset, for use as model training
(and backtesting) data.

Source: https://github.com/vaastav/Fantasy-Premier-League
"""

from pathlib import Path

import requests

RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"

# Seasons used to train the model.
TRAIN_SEASONS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

# Season the trained model is backtested against (held out, never trained on).
TEST_SEASON = "2025-26"

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"


def download_season(season: str) -> Path:
    url = f"{RAW_BASE}/{season}/gws/merged_gw.csv"
    response = requests.get(url, timeout=60)
    response.raise_for_status()

    season_dir = OUTPUT_DIR / season
    season_dir.mkdir(parents=True, exist_ok=True)
    out_path = season_dir / "merged_gw.csv"
    out_path.write_bytes(response.content)
    return out_path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for season in TRAIN_SEASONS + [TEST_SEASON]:
        path = download_season(season)
        n_lines = sum(1 for _ in path.open(encoding="utf-8", errors="ignore")) - 1
        role = "test (held out)" if season == TEST_SEASON else "train"
        print(f"{season} [{role}] -> {path} ({n_lines} rows)")


if __name__ == "__main__":
    main()
