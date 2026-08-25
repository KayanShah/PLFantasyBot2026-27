"""
Generates every strategy's recommended squad against the live FPL API --
dry run only, nothing is ever submitted. This is the multi-strategy
counterpart to live_pipeline.py, which is left completely untouched: the
real FPL account is only ever touched by live_pipeline.py's own --apply
path, which already matches strategies.STRATEGIES["balanced"]'s settings
(TRANSFER_MARGIN=1.5, lookahead 5, no ownership tilt -- those are
simulate_season's tuned defaults, which live_pipeline.py imports directly).

Each strategy is a "shadow" squad with no real FPL entry behind it, so its
current-squad state has to live somewhere between runs:
    data/live_state_{key}.json   what it currently holds, bank, free transfers

Before any gameweek is played there is no state yet, so every strategy starts
from the same from-scratch 15-man build live_pipeline.py itself uses for GW1
(see choose_team()'s current=None branch). Re-running this after a gameweek
has been played rolls each strategy's shadow squad forward the same way
plan_transfers() already does for the real team -- what it does NOT yet do is
score a past gameweek's held squad against the real result to build a running
total; that needs real 2026-27 results to exist first; wiring it in is the
next piece once GW1 has actually been played.

Output, in the shape build_site.py already reads from the backtest
(model/run_all_strategies.py):
    data/live_squads_{key}.json
    data/strategies_manifest_2026-27.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import train_model
from live_pipeline import (
    DATA_DIR, LIVE_TRAIN_SEASONS, PRIOR_SEASON, SEASON, STARTING_BUDGET,
    build_predictions, choose_team, fetch, next_gameweek, sync_season,
    unavailable_elements,
)
from simulate_season import ENSEMBLE_EXTRA_SEEDS, apply_differential_tilt, load_team_names
from strategies import ordered

OUT_DIR = Path(__file__).resolve().parent.parent / "data"


def provisionally_finished_gws(bootstrap: dict, fixtures: list[dict]) -> list[int]:
    """
    Gameweeks where every fixture has a final score (finished_provisional),
    even if FPL's own event.finished flag hasn't flipped yet -- confirmed for
    real for GW1 2026-27: all 10 fixtures finished_provisional with real
    scores while event.finished was still False, matches ended a day earlier.
    That flag waits on a separate bonus-points/data-check pass. Good enough
    to sync real results and score a squad against; deadline gating
    elsewhere still compares against the real deadline time directly, not
    this.
    """
    by_gw: dict[int, list[dict]] = {}
    for f in fixtures:
        if f.get("event") is None:
            continue
        by_gw.setdefault(f["event"], []).append(f)
    return sorted(gw for gw, fx in by_gw.items() if fx and all(f.get("finished_provisional") for f in fx))


def next_planning_gameweek(bootstrap: dict) -> dict:
    """
    Which gameweek to plan transfers for. next_gameweek() gates on the same
    lagging official `finished` flag, which can leave it pointing at a
    gameweek whose deadline has already passed. Once that's true there is
    nothing left to plan for that gameweek regardless of the flag, so fall
    through to the following event instead of getting stuck re-targeting one
    that has already locked.
    """
    event = next_gameweek(bootstrap)
    deadline = datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) < deadline:
        return event
    later = [e for e in sorted(bootstrap["events"], key=lambda e: e["id"]) if e["id"] > event["id"]]
    return later[0] if later else event


def _photo_code(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def live_player_entry(row: pd.Series, team_names: dict, captain_id, vice_id) -> dict:
    """
    Same shape as simulate_season.player_entry(), but for a gameweek that
    hasn't been played yet -- there is no real outcome to look up, so points
    are always 0/not-played rather than routed through real_outcome().
    """
    opponent = team_names.get(row.get("opponent_team"), "—")
    venue = "H" if row.get("was_home") == 1 else "A"
    ownership = row.get("selected_by_percent")
    chance = row.get("chance_of_playing_next_round")
    status = row.get("status")
    return {
        "element": int(row["element"]),
        "name": row["name"],
        "team": row["team"],
        "position": row["position_label"],
        "opponent": f"{opponent} ({venue})" if row.get("opponent_team") else "-",
        "difficulty": int(row["difficulty"]) if pd.notna(row.get("difficulty")) else None,
        "points": 0,
        "played": False,
        "is_captain": bool(row["element"] == captain_id),
        "is_vice_captain": bool(row["element"] == vice_id),
        "is_effective_captain": bool(row["element"] == captain_id),
        "is_triple_captain": False,
        "photo_code": _photo_code(row.get("player_code")),
        "ownership": float(ownership) if pd.notna(ownership) else None,
        "status": status if pd.notna(status) and status != "a" else None,
        "news": row["news"].strip() if isinstance(row.get("news"), str) and row["news"].strip() else None,
        "chance_of_playing": int(chance) if pd.notna(chance) else None,
    }


def backfill_element_ids(entry: dict, code_to_element: dict[int, int]) -> None:
    """
    Squads saved before live_player_entry() started carrying `element`
    directly only have `photo_code` (== the player's permanent `code`) to go
    on. Patches `element` back in from that via the live bootstrap's
    code->id map, in place, so old saved gameweeks can still be scored
    without needing a separate one-time migration pass.
    """
    for p in entry["starting_xi"] + entry["bench"]:
        if "element" not in p and p.get("photo_code") is not None:
            elem = code_to_element.get(p["photo_code"])
            if elem is not None:
                p["element"] = elem


def real_outcome(element: int, live_results: dict[int, dict]) -> tuple[int, int]:
    row = live_results.get(element)
    if row is None:
        return 0, 0
    return row["total_points"], row["minutes"]


def apply_auto_subs(starting_xi: list[dict], bench: list[dict], live_results: dict[int, dict]) -> list[int]:
    """
    Mirrors simulate_season.apply_auto_subs exactly, operating on this
    pipeline's saved JSON player-dict shape instead of DataFrames -- a live
    squad only exists as JSON between separate script runs, there's no
    DataFrame to carry forward. Starters with 0 real minutes are replaced by
    the first bench player (in order) with real minutes, subject to the
    resulting XI staying a legal formation (1 GKP, >=3 DEF, >=2 MID, >=1 FWD).
    """
    final_ids = [p["element"] for p in starting_xi]
    positions = {p["element"]: p["position"] for p in starting_xi}

    for starter in starting_xi:
        _, mins = real_outcome(starter["element"], live_results)
        if mins > 0:
            continue
        for sub in bench:
            if sub["element"] in final_ids:
                continue
            _, sub_mins = real_outcome(sub["element"], live_results)
            if sub_mins == 0:
                continue
            trial = [positions[e] if e != starter["element"] else sub["position"] for e in final_ids]
            counts = {p: trial.count(p) for p in ["GKP", "DEF", "MID", "FWD"]}
            if counts.get("GKP", 0) == 1 and counts.get("DEF", 0) >= 3 and counts.get("MID", 0) >= 2 and counts.get("FWD", 0) >= 1:
                final_ids[final_ids.index(starter["element"])] = sub["element"]
                positions[sub["element"]] = sub["position"]
                break
    return final_ids


def score_gameweek_entry(entry: dict, live_results: dict[int, dict], prior_season_total: int) -> dict:
    """
    Replaces a "not yet played" gameweek entry's placeholder points with real
    results, in place -- same rules simulate_season.py's backtest scoring
    uses (auto-subs, effective-captain fallback, chip handling, -4 per hit),
    just re-derived here since the live squads only exist as saved JSON
    between runs, not as the DataFrames the backtest scores directly.
    """
    def with_real_result(p: dict) -> dict:
        pts, mins = real_outcome(p["element"], live_results)
        return {**p, "points": pts, "played": mins > 0}

    starting_xi = [with_real_result(p) for p in entry["starting_xi"]]
    bench = [with_real_result(p) for p in entry["bench"]]

    final_ids = apply_auto_subs(starting_xi, bench, live_results)

    captain = next((p for p in starting_xi if p["is_captain"]), None)
    vice = next((p for p in starting_xi if p["is_vice_captain"]), None)
    effective = captain
    if captain and not captain["played"] and vice and vice["played"]:
        effective = vice
    for p in starting_xi:
        p["is_effective_captain"] = bool(effective and p["element"] == effective["element"])

    multiplier = 3 if any(p.get("is_triple_captain") for p in starting_xi) else 2
    starting_points = sum(real_outcome(e, live_results)[0] for e in final_ids)
    captain_pts = real_outcome(effective["element"], live_results)[0] if effective else 0
    gw_score = starting_points + captain_pts * (multiplier - 1)

    if entry.get("chip") == "Bench Boost":
        bench_ids = [p["element"] for p in bench if p["element"] not in final_ids]
        gw_score += sum(real_outcome(e, live_results)[0] for e in bench_ids)

    gw_score -= 4 * (entry.get("hits") or 0)

    entry["starting_xi"] = starting_xi
    entry["bench"] = bench
    entry["gw_score"] = round(gw_score)
    entry["season_total"] = round(prior_season_total + gw_score)
    return entry


def load_shadow_state(key: str) -> dict | None:
    path = OUT_DIR / f"live_state_{key}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def squad_bank(choice: dict) -> int:
    """
    Tenths of a million left over after buying the chosen squad. Not just the
    GW1 STARTING_BUDGET case -- plan_transfers()'s result carries a "cost"
    column (sell-value-priced for retained players) whenever a real squad
    exists to transfer from, "value" (live buy price) otherwise, and
    choice["budget"] is already the right total to compare against either way
    (STARTING_BUDGET from scratch, sell-value + bank when transferring).
    """
    cost_col = "cost" if "cost" in choice["squad"].columns else "value"
    return choice["budget"] - int(choice["squad"][cost_col].sum())


def save_shadow_state(key: str, choice: dict, free_transfers_next: int) -> None:
    path = OUT_DIR / f"live_state_{key}.json"
    picks = [
        {"element": int(e), "selling_price": int(v)}
        for e, v in zip(choice["squad"]["element"], choice["squad"]["value"])
    ]
    state = {"picks": picks, "transfers": {"bank": squad_bank(choice), "limit": free_transfers_next}}
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> None:
    print("Fetching live FPL data...")
    bootstrap = fetch("bootstrap-static/")
    fixtures = fetch("fixtures/")

    event = next_planning_gameweek(bootstrap)
    gw = event["id"]
    deadline = datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    finished = provisionally_finished_gws(bootstrap, fixtures)

    # Real per-gameweek results for scoring, fetched once and reused across
    # every strategy -- built straight from the live API rather than
    # sync_season()'s merged_gw.csv, so scoring never depends on that having
    # collapsed doubles/duplicates the exact way the model-training path needs.
    live_results_by_gw: dict[int, dict[int, dict]] = {}
    for finished_gw in finished:
        live = fetch(f"event/{finished_gw}/live/")
        live_results_by_gw[finished_gw] = {e["id"]: e["stats"] for e in live["elements"]}

    # For backfill_element_ids() -- squads saved before `element` was added
    # to live_player_entry()'s output only have `photo_code` to identify a
    # player by.
    code_to_element = {e["code"]: e["id"] for e in bootstrap["elements"]}

    # The whole season's calendar, not just the next deadline -- the website
    # shows this as a browsable dropdown so a manager can see what's coming,
    # not just what's due right now. "finished" here is the provisional
    # signal too, so a just-played gameweek reads as done on the site well
    # before FPL's official flag catches up. Not strategy-specific, so
    # written once here rather than duplicated into every strategy's squad file.
    calendar = [
        {"gw": e["id"], "deadline": e["deadline_time"], "finished": e["id"] in finished}
        for e in bootstrap["events"]
    ]
    (OUT_DIR / "live_gameweek_calendar.json").write_text(json.dumps(calendar, indent=2), encoding="utf-8")

    print(f"Target: GW{gw} ({len(finished)} finished gameweek(s) so far this season), "
          f"deadline {deadline.isoformat()}")

    print(f"Syncing {SEASON} -> {DATA_DIR / SEASON}")
    sync_season(bootstrap, fixtures, finished)
    team_names = load_team_names(SEASON)

    train_model.TRAIN_SEASONS = LIVE_TRAIN_SEASONS
    print(f"Training {1 + len(ENSEMBLE_EXTRA_SEEDS)}-model ensemble on "
          f"{LIVE_TRAIN_SEASONS[0]} -> {LIVE_TRAIN_SEASONS[-1]}...")
    models = [train_model.train_baseline_model()] + [
        train_model.train_baseline_model(seed=s) for s in ENSEMBLE_EXTRA_SEEDS
    ]

    unavailable = unavailable_elements(bootstrap)
    if unavailable:
        print(f"{len(unavailable)} player(s) flagged unavailable by the API, excluded from every buy pool.")

    # Built once at the widest lookahead any strategy needs (5) -- a strategy
    # asking for a shorter lookahead just ignores the extra future-GW rows
    # (build_horizon_scores only sums gw..gw+lookahead-1), so this is exactly
    # as correct as building it per-strategy, without retraining/rebuilding
    # predictions four times for what only differs in how far they're summed.
    max_lookahead = max(cfg["lookahead_gws"] for _, cfg in ordered())
    print(f"Building live predictions (lookahead {max_lookahead} GWs)...")
    base_predictions = build_predictions(models, bootstrap, fixtures, gw, max_lookahead)

    manifest_path = OUT_DIR / "strategies_manifest_2026-27.json"
    # Read-merge-write, not overwrite: generate_live_xg_strategy.py writes its
    # own entry into this same manifest, and each script only owns the keys
    # it produces -- an overwrite here would silently drop xg_experimental
    # every time this one runs after it (e.g. on every refresh-dashboard.yml).
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {"season": SEASON, "strategies": []}
    )
    own_keys = {key for key, _ in ordered()}
    manifest["strategies"] = [s for s in manifest["strategies"] if s["key"] not in own_keys]

    for key, cfg in ordered():
        print(f"\n=== {cfg['label']} ({key}) ===")

        squads_path = OUT_DIR / f"live_squads_{key}.json"
        existing = json.loads(squads_path.read_text(encoding="utf-8")) if squads_path.exists() else None
        gameweeks_history = existing["gameweeks"] if existing else []

        # Score any finished gameweek this strategy hasn't been scored for
        # yet, oldest first, so season_total carries forward correctly.
        # score_gameweek_entry() mutates each entry in place (also the same
        # object sitting in gameweeks_history -- no reassignment needed).
        prior_season_total = 0
        for g in sorted(gameweeks_history, key=lambda g: g["gw"]):
            if g["gw"] in finished and g.get("season_total") is None:
                backfill_element_ids(g, code_to_element)
                score_gameweek_entry(g, live_results_by_gw[g["gw"]], prior_season_total)
                print(f"  Scored GW{g['gw']}: {g['gw_score']} pts (season total so far: {g['season_total']})")
            if g.get("season_total") is not None:
                prior_season_total = g["season_total"]

        state = load_shadow_state(key)
        current = state if state else None
        free_transfers = (state["transfers"]["limit"] if state else 1)
        bank = (state["transfers"]["bank"] if state else 0)
        # Unlimited-rebuild eligibility ends at GW1's own deadline, not just
        # whenever FPL happens to mark GW1 "finished" (which can be days
        # later, once its matches conclude) -- see live_pipeline.py's
        # matching fix for why `not finished` alone isn't enough. `finished`
        # itself is now the provisional signal, so this also correctly
        # switches a scored gameweek's squad from "freely rebuildable" to
        # "only reachable via normal transfers" as soon as real results
        # exist for it, not days later.
        unlimited = current is not None and not finished and now < deadline

        predictions = apply_differential_tilt(base_predictions, cfg["differential_weight"])
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
        # Replace this gw's entry if re-run before its own deadline (e.g. a
        # manual refresh with fresh prices), keep every other gw untouched.
        gameweeks_history = [g for g in gameweeks_history if g["gw"] != gw] + [new_entry]
        gameweeks_history.sort(key=lambda g: g["gw"])

        squads_path.write_text(json.dumps({
            "season": SEASON,
            "final_score": None,
            "gameweeks": gameweeks_history,
        }, indent=2), encoding="utf-8")

        # Mirrors simulate_season.simulate()'s exact roll-forward rule: a full
        # rebuild (GW1, or the unlimited pre-deadline rebuild) doesn't touch
        # the free-transfer count at all; otherwise only the free transfers
        # actually spent are deducted before next week's top-up.
        if current is None or unlimited:
            next_free_transfers = free_transfers
        else:
            used_free = min(choice["transfers"], free_transfers)
            next_free_transfers = min(5, (free_transfers - used_free) + 1)
        save_shadow_state(key, choice, next_free_transfers)

        manifest["strategies"].append({
            "key": key, "label": cfg["label"], "short": cfg["short"],
            "description": cfg["description"], "risk": cfg["risk"],
            "risk_tier": cfg["risk_tier"], "order": cfg["order"],
            "final_score": None, "squads_file": squads_path.name,
        })
        print(f"  {len(choice['xi'])} starters, {choice['transfers']} transfer(s), {choice['hits']} hit(s)")

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
