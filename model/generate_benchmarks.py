"""
Public benchmarks for the live 2026-27 season, from data every strategy on
this dashboard is otherwise judged in isolation against: the real average
manager's score, and a handful of season records. All of it comes from one
already-unauthenticated bootstrap-static fetch -- no manager ID, no
login, no extra API calls beyond what every other live script here already
makes.

    manager_average_cumulative   Sum of events[].average_entry_score across
                                  every finished gameweek -- the real number
                                  every strategy's season total is actually
                                  being compared against elsewhere on this
                                  site (previously only ever computed ad hoc,
                                  by hand, each time someone asked).
    highest_gameweek_score        The single best score any manager posted
                                  in one gameweek this season, and which one.
    top_scorer / most_owned /
    most_transferred_in /
    most_bonus                    Season-aggregate fields FPL already tracks
                                  per player (elements[].total_points /
                                  .selected_by_percent / .transfers_in /
                                  .bonus) -- no per-gameweek summing needed,
                                  they're already cumulative in bootstrap-static.
    most_captained_latest         Not a season aggregate -- FPL only ever
                                  reports most_captained per gameweek, with
                                  no public per-manager pick history to sum
                                  across weeks from. The latest finished
                                  gameweek's is the closest honest equivalent.

Past seasons aren't included here -- once a season ends, the live API stops
serving it (see plan.md Phase 6), so this only ever has something to say
about the *current* season. 2025-26's benchmark figures are the fixed,
already-validated numbers baked into build_site.py directly.
"""

import json
from pathlib import Path

from generate_live_strategies import provisionally_finished_gws
from live_pipeline import fetch

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "live_benchmarks_2026-27.json"


def player_name(element: dict) -> str:
    return f"{element['first_name']} {element['second_name']}"


def main() -> None:
    print("Fetching live FPL data...")
    bootstrap = fetch("bootstrap-static/")
    fixtures = fetch("fixtures/")
    finished = provisionally_finished_gws(bootstrap, fixtures)

    finished_events = [e for e in bootstrap["events"] if e["id"] in finished]
    manager_average_cumulative = sum(e["average_entry_score"] or 0 for e in finished_events)

    highest_gw_event = max(finished_events, key=lambda e: e["highest_score"] or 0, default=None)
    highest_gameweek_score = (
        {"gw": highest_gw_event["id"], "score": highest_gw_event["highest_score"]}
        if highest_gw_event else None
    )

    elements = bootstrap["elements"]
    elements_by_id = {e["id"]: e for e in elements}
    top_scorer = max(elements, key=lambda e: e["total_points"])
    most_owned = max(elements, key=lambda e: float(e["selected_by_percent"]))
    most_transferred_in = max(elements, key=lambda e: e["transfers_in"])
    most_bonus = max(elements, key=lambda e: e["bonus"])

    # most_captained is only ever reported per gameweek, not as a season
    # aggregate FPL exposes anywhere public -- the latest finished
    # gameweek's is the closest real, correctly-labelled equivalent, not a
    # sum across gameweeks (which would need per-manager pick data this
    # endpoint doesn't have).
    latest_finished = finished_events[-1] if finished_events else None
    most_captained_latest = (
        {
            "gw": latest_finished["id"],
            "name": player_name(elements_by_id[latest_finished["most_captained"]]),
        }
        if latest_finished and latest_finished.get("most_captained") is not None else None
    )

    data = {
        "season": "2026-27",
        "through_gw": max(finished) if finished else 0,
        "manager_average_cumulative": manager_average_cumulative,
        "highest_gameweek_score": highest_gameweek_score,
        "top_scorer": {"name": player_name(top_scorer), "points": top_scorer["total_points"]},
        "most_owned": {"name": player_name(most_owned), "percent": float(most_owned["selected_by_percent"])},
        "most_bonus": {"name": player_name(most_bonus), "points": most_bonus["bonus"]},
        "most_captained_latest": most_captained_latest,
        "most_transferred_in": {"name": player_name(most_transferred_in), "count": most_transferred_in["transfers_in"]},
    }
    OUT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    for key, value in data.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
