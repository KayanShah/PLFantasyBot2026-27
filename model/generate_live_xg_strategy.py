"""
Live counterpart to run_xg_strategy.py: Balanced's exact policy against the
live 2026-27 API, but with xG/xA features enabled -- dry run only, never
submits. Kept as its own process for the same reason as the backtest
version: enable_xg_features() mutates train_model's feature-column globals
in place, which would silently change what generate_live_strategies.py's
four strategies train on if run in the same process.

Training seasons: every season with real xG data (2022-23 GW16 onward),
nothing held out -- unlike the 2025-26 backtest version, live has no future
season to hold out, so this uses one more season (2025-26) than
run_xg_strategy.py's XG_TRAIN_SEASONS does. Same underlying reason
live_pipeline.py's LIVE_TRAIN_SEASONS has one more season than
train_model.TRAIN_SEASONS.

The same open question as the backtest version applies here: this is not an
isolated test of xG, since the season range also differs from Balanced's own
live training range. See run_xg_strategy.py / plan.md.

Output, merged into the same manifest generate_live_strategies.py writes
(each script only touches its own "xg_experimental" / four-strategy keys):
    data/live_squads_xg_experimental.json
    data/live_state_xg_experimental.json
    data/strategies_manifest_2026-27.json
"""

import json
from pathlib import Path

import train_model
from generate_live_strategies import live_player_entry, load_shadow_state, save_shadow_state
from live_pipeline import (
    DATA_DIR, SEASON, build_predictions,
    choose_team, fetch, next_gameweek, sync_season, unavailable_elements,
)
from simulate_season import ENSEMBLE_EXTRA_SEEDS, load_team_names
from strategies import STRATEGIES

OUT_DIR = Path(__file__).resolve().parent.parent / "data"
KEY = "xg_experimental"

# xG exists from 2022-23 GW16 onward. Live has nothing to hold out (unlike
# the 2025-26 backtest, which stops at 2024-25), so this includes 2025-26 --
# one more season than run_xg_strategy.py's XG_TRAIN_SEASONS.
XG_LIVE_TRAIN_SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]


def main() -> None:
    print("Fetching live FPL data...")
    bootstrap = fetch("bootstrap-static/")
    fixtures = fetch("fixtures/")

    event = next_gameweek(bootstrap)
    gw = event["id"]
    finished = [e["id"] for e in bootstrap["events"] if e["finished"]]
    print(f"Target: GW{gw} ({len(finished)} finished gameweek(s) so far this season)")

    print(f"Syncing {SEASON} -> {DATA_DIR / SEASON}")
    sync_season(bootstrap, fixtures, finished)
    team_names = load_team_names(SEASON)

    train_model.enable_xg_features()
    train_model.TRAIN_SEASONS = XG_LIVE_TRAIN_SEASONS
    print(f"Training {1 + len(ENSEMBLE_EXTRA_SEEDS)}-model ensemble on "
          f"{XG_LIVE_TRAIN_SEASONS[0]} -> {XG_LIVE_TRAIN_SEASONS[-1]} (xG/xA features ON)...")
    models = [train_model.train_baseline_model()] + [
        train_model.train_baseline_model(seed=s) for s in ENSEMBLE_EXTRA_SEEDS
    ]

    unavailable = unavailable_elements(bootstrap)

    cfg = STRATEGIES["balanced"]
    print(f"Building live predictions (lookahead {cfg['lookahead_gws']} GWs)...")
    predictions = build_predictions(models, bootstrap, fixtures, gw, cfg["lookahead_gws"])

    state = load_shadow_state(KEY)
    current = state if state else None
    free_transfers = state["transfers"]["limit"] if state else 1
    bank = state["transfers"]["bank"] if state else 0
    unlimited = current is not None and not finished

    choice = choose_team(
        predictions, gw, models, current, free_transfers, bank,
        unavailable, cfg["lookahead_gws"], unlimited,
    )

    starting_xi = [
        live_player_entry(row, team_names, choice["captain"], choice["vice"])
        for _, row in choice["xi"].iterrows()
    ]
    bench = [
        live_player_entry(row, team_names, choice["captain"], choice["vice"])
        for _, row in choice["bench"].iterrows()
    ]

    squads_path = OUT_DIR / f"live_squads_{KEY}.json"
    squads_path.write_text(json.dumps({
        "season": SEASON,
        "final_score": None,
        "gameweeks": [{
            "gw": int(gw), "chip": "", "transfers": choice["transfers"],
            "hits": choice["hits"], "gw_score": None, "season_total": None,
            "starting_xi": starting_xi, "bench": bench,
        }],
    }, indent=2), encoding="utf-8")

    if current is None or unlimited:
        next_free_transfers = free_transfers
    else:
        used_free = min(choice["transfers"], free_transfers)
        next_free_transfers = min(5, (free_transfers - used_free) + 1)
    save_shadow_state(KEY, choice, next_free_transfers)

    manifest_path = OUT_DIR / "strategies_manifest_2026-27.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {"season": SEASON, "strategies": []}
    )
    manifest["strategies"] = [s for s in manifest["strategies"] if s["key"] != KEY] + [{
        "key": KEY, "label": "xG Experimental", "short": "Balanced's policy, xG/xA features on.",
        "description": (
            "Identical policy to Balanced -- the only difference is the prediction "
            "model trains with expected-goals/assists features on, using every "
            "season with real xG data (2022-23 GW16 onward). Scored higher than "
            "Balanced on the 2025-26 backtest (2100 vs 2049), but that comparison "
            "trains on a different set of seasons too, so it isn't a clean isolated "
            "test of xG alone -- see the caution note on the 2025-26 Backtest tab "
            "and plan.md before treating this as a recommendation."
        ),
        "risk": "Experimental", "risk_tier": "medium", "order": 4,
        "final_score": None, "squads_file": squads_path.name,
    }]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\n{len(choice['xi'])} starters, {choice['transfers']} transfer(s), {choice['hits']} hit(s)")
    print(f"Wrote {squads_path}")
    print(f"Updated manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
