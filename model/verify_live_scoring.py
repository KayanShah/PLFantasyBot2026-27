"""
Regression check for the real-results scoring logic added in
generate_live_strategies.py (real_outcome/apply_auto_subs/score_gameweek_entry/
backfill_element_ids). Real money-adjacent decisions are read straight off
this arithmetic once a gameweek is scored, so it's worth a permanent,
hand-checked fixture rather than trusting it silently -- plain asserts,
matching this project's existing style (multi_season_backtest.py etc.),
no new test framework/dependency for one fixture.

Fixture: an 11-man XI + 4-man bench where
  - most starters played and scored normally
  - two starters blank (elements 5 and 7, both MID) but only one bench
    player has real minutes (element 13) -- apply_auto_subs() walks
    starting_xi in order, so the FIRST blanked starter it reaches (element
    5, which is also the captain) gets the sub; element 7 has no eligible
    replacement left and stays in the XI scoring 0
  - the captain (element 5) blanked, the vice (element 8) played, so the
    vice becomes effective captain and doubles
  - one hit was taken (-4)

Hand-calculated: final XI after the sub is elements
[1,2,3,4,13,6,7,8,9,10,11] with points [3,6,2,6,7,8,0,4,2,1,3] = 42, plus
the effective captain's (element 8, 4 pts) extra multiplier (+4), minus the
hit (-4) = 42 gw_score.

Run directly: python3 verify_live_scoring.py
"""

from generate_live_strategies import backfill_element_ids, score_gameweek_entry


def player(element, position, **overrides):
    p = {
        "element": element, "name": f"P{element}", "team": "TST", "position": position,
        "opponent": "OPP (H)", "difficulty": 3, "points": 0, "played": False,
        "is_captain": False, "is_vice_captain": False, "is_effective_captain": False,
        "is_triple_captain": False, "photo_code": 1000 + element, "ownership": 10.0,
        "status": None, "news": None, "chance_of_playing": None,
    }
    p.update(overrides)
    return p


def main() -> None:
    live_results = {
        1: {"total_points": 3, "minutes": 90},
        2: {"total_points": 6, "minutes": 90},
        3: {"total_points": 2, "minutes": 90},
        4: {"total_points": 6, "minutes": 90},
        5: {"total_points": 0, "minutes": 0},    # captain, blanked
        6: {"total_points": 8, "minutes": 90},
        7: {"total_points": 0, "minutes": 0},    # blanks -- needs auto-sub
        8: {"total_points": 4, "minutes": 90},   # vice, played -> effective captain
        9: {"total_points": 2, "minutes": 90},
        10: {"total_points": 1, "minutes": 90},
        11: {"total_points": 3, "minutes": 90},
        12: {"total_points": 2, "minutes": 90},  # bench GKP, unused
        13: {"total_points": 7, "minutes": 90},  # first bench outfield w/ mins -- subs in for 7
        14: {"total_points": 0, "minutes": 0},   # bench, didn't play
        15: {"total_points": 0, "minutes": 0},   # bench, didn't play
    }

    starting_xi = [
        player(1, "GKP"),
        player(2, "DEF"), player(3, "DEF"), player(4, "DEF"),
        player(5, "MID", is_captain=True),
        player(6, "MID"),
        player(7, "MID"),
        player(8, "FWD", is_vice_captain=True),
        player(9, "FWD"), player(10, "FWD"), player(11, "DEF"),
    ]
    bench = [
        player(12, "GKP"),
        player(13, "MID"),
        player(14, "DEF"),
        player(15, "FWD"),
    ]

    entry = {
        "gw": 1, "chip": "", "transfers": 1, "hits": 1, "gw_score": None,
        "season_total": None, "starting_xi": starting_xi, "bench": bench,
    }

    scored = score_gameweek_entry(entry, live_results, prior_season_total=0)

    final_ids = [p["element"] for p in scored["starting_xi"]]
    assert final_ids == [1, 2, 3, 4, 13, 6, 7, 8, 9, 10, 11], f"auto-sub didn't fire as expected: {final_ids}"

    eff = next(p for p in scored["starting_xi"] if p["is_effective_captain"])
    assert eff["element"] == 8, f"effective captain should be element 8 (vice), got {eff['element']}"

    assert scored["gw_score"] == 42, f"expected gw_score 42, got {scored['gw_score']}"
    assert scored["season_total"] == 42, f"expected season_total 42, got {scored['season_total']}"

    # backfill_element_ids: a legacy entry with only photo_code should get
    # element resolved back via the live bootstrap's code->id map.
    legacy = {"starting_xi": [{"photo_code": 1099, "position": "FWD"}], "bench": []}
    backfill_element_ids(legacy, code_to_element={1099: 999})
    assert legacy["starting_xi"][0]["element"] == 999, "backfill_element_ids didn't resolve element"

    print(f"All checks passed. gw_score={scored['gw_score']} (expected 42)")


if __name__ == "__main__":
    main()
