"""
Generates the next-gameweek recommendation for "Live Updated Team" -- the
real squad actually held in the FPL app, tracked separately from the five
model strategies (see plan.md). Consolidates a pattern that had been
rewritten from scratch in an ad hoc script three separate times this month
(each time re-deriving the same training/prediction/scoring flow), which is
exactly the kind of duplication a committed script exists to prevent.

Two files carry different things and are NOT interchangeable:
    data/live_state_real_team.json    ground truth for what's currently
                                       held -- real buy prices, so bank is
                                       computed from real sell values
                                       (simulate_season.sell_value()), not a
                                       flat-budget approximation. This file
                                       is updated BY HAND whenever the real
                                       squad actually changes (a transfer
                                       made, or a different pick than this
                                       script recommended) -- this script
                                       only ever reads it, never writes the
                                       *picks*, since "what's actually held"
                                       isn't something a script can know
                                       without the user reporting it.
    data/live_squads_real_team.json   display history + the current
                                       recommendation. Finished gameweeks
                                       get scored once and never touched
                                       again; the next unplayed gameweek's
                                       entry is a recommendation, freshly
                                       recomputed by this script each run
                                       (same convention the five model
                                       strategies use for their own
                                       not-yet-played entry) -- it is NOT
                                       "what will be done," just "what the
                                       model suggests as of this run."

Not run by refresh-dashboard.yml -- that workflow only owns the five model
strategies (see the fix in its "Commit and push" step) and would silently
revert any hand-updated live_state_real_team.json picks if it tried.
Run this by hand after recording what actually happened for a finished
gameweek, or whenever you want a fresher recommendation ahead of a deadline.
"""

import json
from pathlib import Path

import train_model
from generate_live_strategies import (
    backfill_element_ids, live_player_entry, next_planning_gameweek,
    provisionally_finished_gws, score_gameweek_entry, squad_bank,
)
from live_pipeline import (
    LIVE_TRAIN_SEASONS, build_predictions, choose_team, fetch, sync_season,
    unavailable_elements,
)
from simulate_season import ENSEMBLE_EXTRA_SEEDS, sell_value

OUT_DIR = Path(__file__).resolve().parent.parent / "data"
KEY = "real_team"


def main() -> None:
    print("Fetching live FPL data...")
    bootstrap = fetch("bootstrap-static/")
    fixtures = fetch("fixtures/")
    finished = provisionally_finished_gws(bootstrap, fixtures)
    event = next_planning_gameweek(bootstrap)
    gw = event["id"]
    print(f"Target GW{gw}, finished so far: {finished}")

    sync_season(bootstrap, fixtures, finished)
    team_names = {t["id"]: t["name"] for t in bootstrap["teams"]}
    now_cost = {e["id"]: e["now_cost"] for e in bootstrap["elements"]}

    squads_path = OUT_DIR / f"live_squads_{KEY}.json"
    d = json.loads(squads_path.read_text(encoding="utf-8"))
    gws = sorted(d["gameweeks"], key=lambda g: g["gw"])

    # For backfill_element_ids() -- matches generate_live_strategies.py's own
    # scoring loop, so any entry missing `element` (only ever a risk for data
    # saved before that field existed) doesn't crash real_team's scoring the
    # same way it once crashed the five model strategies'.
    code_to_element = {e["code"]: e["id"] for e in bootstrap["elements"]}

    # Score any finished gameweek not yet scored, oldest first.
    prior_total = 0
    for g in gws:
        if g["gw"] in finished and g.get("season_total") is None:
            backfill_element_ids(g, code_to_element)
            live = fetch(f"event/{g['gw']}/live/")
            live_results = {e["id"]: e["stats"] for e in live["elements"]}
            score_gameweek_entry(g, live_results, prior_season_total=prior_total)
            print(f"  Scored GW{g['gw']}: {g['gw_score']} pts (season total: {g['season_total']})")
        if g.get("season_total") is not None:
            prior_total = g["season_total"]

    # Recommendation for the next gameweek, from the REAL held squad.
    # selling_price is refreshed here from each pick's *real* buy_price via
    # sell_value() (simulate_season's own FPL profit-share rule, reused
    # rather than reimplemented -- an earlier version of this duplicated it
    # under a different name before this was noticed) -- now_cost drifts
    # between runs, so this is not a one-time calculation, it's what keeps
    # the budget accurate run to run instead of slowly drifting back into
    # the flat-budget guess this whole mechanism replaced.
    state_path = OUT_DIR / f"live_state_{KEY}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for p in state["picks"]:
        p["selling_price"] = sell_value(p["buy_price"], now_cost[p["element"]])
    current = {"picks": state["picks"]}
    free_transfers = state["transfers"]["limit"]
    bank = state["transfers"]["bank"]

    print(f"Training {1 + len(ENSEMBLE_EXTRA_SEEDS)}-model ensemble...")
    train_model.TRAIN_SEASONS = LIVE_TRAIN_SEASONS
    models = [train_model.train_baseline_model()] + [
        train_model.train_baseline_model(seed=s) for s in ENSEMBLE_EXTRA_SEEDS
    ]
    unavailable = unavailable_elements(bootstrap)
    predictions = build_predictions(models, bootstrap, fixtures, gw, 5)

    choice = choose_team(predictions, gw, models, current, free_transfers, bank, unavailable, 5, False)
    cap, vice = choice["captain"], choice["vice"]
    xi = [live_player_entry(r, team_names, cap, vice) for _, r in choice["xi"].iterrows()]
    bench = [live_player_entry(r, team_names, cap, vice) for _, r in choice["bench"].iterrows()]

    held_ids = {p["element"] for p in current["picks"]}
    new_ids = set(choice["xi"]["element"]) | set(choice["bench"]["element"])
    new_entry = {
        "gw": int(gw), "chip": "", "transfers": choice["transfers"], "hits": choice["hits"],
        "gw_score": None, "season_total": None, "deadline": event["deadline_time"],
        "bank": round(squad_bank(choice) / 10, 1),
        "starting_xi": xi, "bench": bench,
    }
    id_name = {e["id"]: e["web_name"] for e in bootstrap["elements"]}
    print(f"GW{gw} OUT: {[id_name[i] for i in held_ids - new_ids]}")
    print(f"GW{gw} IN: {[id_name[i] for i in new_ids - held_ids]}")
    print(f"GW{gw} transfers={choice['transfers']} hits={choice['hits']} "
          f"captain={id_name[cap]} vice={id_name[vice]} bank={new_entry['bank']}")

    gws = [g for g in gws if g["gw"] != gw] + [new_entry]
    gws.sort(key=lambda g: g["gw"])
    d["gameweeks"] = gws
    squads_path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    print(f"Wrote {squads_path}")
    print(
        "Note: this is a recommendation, not a record of what was done -- "
        "once real transfers are made, update data/live_state_real_team.json's "
        "picks (real buy prices) by hand and re-run to score/plan from there."
    )


if __name__ == "__main__":
    main()
