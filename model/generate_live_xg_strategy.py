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
from datetime import datetime, timezone
from pathlib import Path

import train_model
from generate_live_strategies import (
    backfill_element_ids, live_player_entry, load_shadow_state, next_planning_gameweek,
    provisionally_finished_gws, save_shadow_state, score_gameweek_entry, squad_bank,
)
from live_pipeline import (
    DATA_DIR, SEASON, build_predictions,
    choose_team, fetch, sync_season, unavailable_elements,
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

    event = next_planning_gameweek(bootstrap)
    gw = event["id"]
    deadline = datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    finished = provisionally_finished_gws(bootstrap, fixtures)
    print(f"Target: GW{gw} ({len(finished)} finished gameweek(s) so far this season), "
          f"deadline {deadline.isoformat()}")

    live_results_by_gw: dict[int, dict[int, dict]] = {}
    for finished_gw in finished:
        live = fetch(f"event/{finished_gw}/live/")
        live_results_by_gw[finished_gw] = {e["id"]: e["stats"] for e in live["elements"]}

    code_to_element = {e["code"]: e["id"] for e in bootstrap["elements"]}

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

    squads_path = OUT_DIR / f"live_squads_{KEY}.json"
    existing = json.loads(squads_path.read_text(encoding="utf-8")) if squads_path.exists() else None
    gameweeks_history = existing["gameweeks"] if existing else []

    prior_season_total = 0
    for g in sorted(gameweeks_history, key=lambda g: g["gw"]):
        if g["gw"] in finished and g.get("season_total") is None:
            backfill_element_ids(g, code_to_element)
            score_gameweek_entry(g, live_results_by_gw[g["gw"]], prior_season_total)
            print(f"  Scored GW{g['gw']}: {g['gw_score']} pts (season total so far: {g['season_total']})")
        if g.get("season_total") is not None:
            prior_season_total = g["season_total"]

    state = load_shadow_state(KEY)
    current = state if state else None
    free_transfers = state["transfers"]["limit"] if state else 1
    bank = state["transfers"]["bank"] if state else 0
    unlimited = current is not None and not finished and now < deadline

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

    new_entry = {
        "gw": int(gw), "chip": "", "transfers": choice["transfers"],
        "hits": choice["hits"], "gw_score": None, "season_total": None,
        "deadline": deadline.isoformat(), "bank": round(squad_bank(choice) / 10, 1),
        "starting_xi": starting_xi, "bench": bench,
    }
    gameweeks_history = [g for g in gameweeks_history if g["gw"] != gw] + [new_entry]
    gameweeks_history.sort(key=lambda g: g["gw"])

    squads_path.write_text(json.dumps({
        "season": SEASON,
        "final_score": None,
        "gameweeks": gameweeks_history,
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
