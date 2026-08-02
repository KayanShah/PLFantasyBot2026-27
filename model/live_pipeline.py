"""
Runs the bot against the live FPL API for the current season, shortly before
each gameweek deadline, and optionally applies the result to a real team.

Everything the backtest does is reused unchanged: the same trained model, the
same feature construction (train_model.build_season_features), the same ILP
squad selection (optimizer) and the same lookahead scoring
(simulate_season.build_horizon_scores). The only genuinely new part is
sourcing the current season from the live API instead of the historical CSVs,
which sync_season() does by writing the same four files per season that
fetch_historical_data.py writes -- so nothing downstream has to know or care
where a season came from.

Live prices are used for every row, including already-played ones. That is
safe because `value` is never a rolling stat: only the gameweek being
predicted feeds its own price to the model, and for that row the live price
is exactly right.

Usage:
    python model/live_pipeline.py                 # dry run, prints the team
    python model/live_pipeline.py --apply         # also submits it
    python model/live_pipeline.py --gw 5          # target a specific gameweek

To run it for real every week, schedule this hourly and let it decide when it
is due, rather than scheduling one job per deadline:

    0 * * * * cd /path/to/repo && python3 model/live_pipeline.py --only-if-due --apply

Environment (only needed for --apply and --check-auth):
    FPL_MANAGER_ID  your entry id, from /entry/<id>/event/1 once signed in
    FPL_COOKIE      the whole `Cookie:` request header copied from a signed-in
                    browser session -- see login() for why, and how
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

import train_model
from optimizer import pick_captains, pick_starting_xi, select_squad
from simulate_season import (
    ENSEMBLE_EXTRA_SEEDS, LOOKAHEAD_GWS, STARTING_BUDGET, TRANSFER_MARGIN,
    build_horizon_scores, ensemble_predict, sell_value,
)

# Player names contain accents; a cp1252 Windows console would otherwise raise
# mid-print and take the run down after the work is already done.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "https://fantasy.premierleague.com/api"
# No login URL any more: users.premierleague.com is gone (NXDOMAIN) and the
# replacement is an OAuth2 flow behind bot protection. See login().

SEASON = "2026-27"
PRIOR_SEASON = "2025-26"

# train_model's default stops at 2024-25 because 2025-26 is its held-out test
# season. Live has nothing to hold out -- every completed season is fair game,
# and leaving the most recent one out would throw away the best data there is.
LIVE_TRAIN_SEASONS = [
    "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
]

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
AUDIT_LOG = Path(__file__).resolve().parent.parent / "data" / "live_decisions.jsonl"

# How long before the deadline the scheduled run is meant to happen. Late enough
# that team news has landed, early enough to leave room for a failed run.
DEADLINE_LEAD = timedelta(minutes=60)

# Running this far ahead of a deadline is allowed but flagged: team news, prices
# and injuries all still move, so the team picked now is not the team you would
# pick at the deadline.
EARLY_RUN_WARNING = timedelta(hours=6)

# Hard ceiling on hits a single automated run may take without a human. A bug
# that wants 5 transfers should stop, not spend 20 points proving it.
MAX_AUTOMATED_HITS = 1

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# Written into merged_gw.csv so load_season() finds what it expects.
STAT_COLUMNS = [
    "minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_missed", "penalties_saved", "yellow_cards",
    "red_cards", "saves", "bonus", "bps", "influence", "creativity",
    "threat", "ict_index", "total_points",
]
MERGED_GW_COLUMNS = [
    "name", "position", "team", "opponent_team", "element", "GW",
    "was_home", "value", "fixture",
] + STAT_COLUMNS


def fetch(path: str, session: requests.Session | None = None) -> dict | list:
    getter = session.get if session else requests.get
    response = getter(f"{API}/{path}", timeout=60)
    response.raise_for_status()
    return response.json()


def team_fixtures(fixtures: list[dict]) -> dict[tuple[int, int], list[dict]]:
    """(team_id, gameweek) -> that team's fixtures, so blanks and doubles fall out naturally."""
    lookup: dict[tuple[int, int], list[dict]] = {}
    for fixture in fixtures:
        if fixture.get("event") is None:
            continue
        for team, opponent, home in [
            (fixture["team_h"], fixture["team_a"], True),
            (fixture["team_a"], fixture["team_h"], False),
        ]:
            lookup.setdefault((team, fixture["event"]), []).append({
                "fixture": fixture["id"],
                "opponent": opponent,
                "was_home": home,
                "difficulty": fixture["team_h_difficulty"] if home else fixture["team_a_difficulty"],
            })
    return lookup


def played_rows(bootstrap: dict, fixtures: list[dict], finished_gws: list[int]) -> list[dict]:
    """One row per (player, finished gameweek), matching merged_gw.csv's shape."""
    teams = {t["id"]: t["name"] for t in bootstrap["teams"]}
    lookup = team_fixtures(fixtures)
    rows = []

    for gw in finished_gws:
        live = fetch(f"event/{gw}/live/")
        stats_by_element = {e["id"]: e["stats"] for e in live.get("elements", [])}
        for element in bootstrap["elements"]:
            played = lookup.get((element["team"], gw))
            if not played:
                continue  # club blanked this gameweek -- no row, same as the historical data
            stats = stats_by_element.get(element["id"], {})
            # One row per fixture, matching the historical files, so
            # load_season() derives fixture_count correctly and a double is
            # visible to the model. The live endpoint already reports gameweek
            # totals across both matches, so those go on the first row and the
            # rest carry zeros -- summing the collapse then reproduces exactly
            # the gameweek total FPL scores, rather than double-counting it.
            for index, fixture in enumerate(played):
                rows.append({
                    "name": f"{element['first_name']} {element['second_name']}",
                    "position": POSITIONS[element["element_type"]],
                    "team": teams[element["team"]],
                    "opponent_team": fixture["opponent"],
                    "element": element["id"],
                    "GW": gw,
                    "was_home": fixture["was_home"],
                    "value": element["now_cost"],
                    "fixture": fixture["fixture"],
                    **{col: (stats.get(col, 0) if index == 0 else 0)
                       for col in STAT_COLUMNS},
                })
    return rows


def upcoming_rows(bootstrap: dict, fixtures: list[dict], gws: list[int]) -> pd.DataFrame:
    """
    Rows for gameweeks that have not been played, so there is something to
    predict against. Stat columns are NaN, not 0, so a rolling window skips
    them rather than treating an unplayed week as a scoreless one.
    """
    teams = {t["id"]: t["name"] for t in bootstrap["teams"]}
    lookup = team_fixtures(fixtures)
    rows = []

    for gw in gws:
        for element in bootstrap["elements"]:
            upcoming = lookup.get((element["team"], gw))
            if not upcoming:
                continue  # blank gameweek -- nothing to predict
            first = upcoming[0]
            rows.append({
                "season": SEASON,
                "name": f"{element['first_name']} {element['second_name']}",
                "position": POSITIONS[element["element_type"]],
                "team": teams[element["team"]],
                "opponent_team": first["opponent"],
                "element": element["id"],
                "GW": gw,
                "was_home": int(first["was_home"]),
                "value": element["now_cost"],
                # Averaged over both fixtures of a double, matching how
                # load_season() collapses one.
                "difficulty": sum(f["difficulty"] for f in upcoming) / len(upcoming),
                "fixture_count": len(upcoming),
                **{col: float("nan") for col in STAT_COLUMNS},
            })
    return pd.DataFrame(rows)


def sync_season(bootstrap: dict, fixtures: list[dict], finished_gws: list[int]) -> None:
    """
    Writes the same four files per season that fetch_historical_data.py writes,
    so load_season()/load_player_codes()/load_team_names() work against the live
    season without knowing it came from the API.
    """
    season_dir = DATA_DIR / SEASON
    season_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame([
        {"id": t["id"], "name": t["name"], "short_name": t["short_name"]}
        for t in bootstrap["teams"]
    ]).to_csv(season_dir / "teams.csv", index=False)

    pd.DataFrame([
        {
            "id": f["id"], "event": f["event"], "kickoff_time": f["kickoff_time"],
            "team_h": f["team_h"], "team_a": f["team_a"],
            "team_h_difficulty": f["team_h_difficulty"],
            "team_a_difficulty": f["team_a_difficulty"],
        }
        for f in fixtures
    ]).to_csv(season_dir / "fixtures.csv", index=False)

    pd.DataFrame([
        {"id": e["id"], "code": e["code"], "first_name": e["first_name"],
         "second_name": e["second_name"], "web_name": e["web_name"]}
        for e in bootstrap["elements"]
    ]).to_csv(season_dir / "players_raw.csv", index=False)

    rows = played_rows(bootstrap, fixtures, finished_gws)
    # Header-only when nothing has been played yet (pre-season), so load_season()
    # still finds the columns it expects instead of a missing file.
    with (season_dir / "merged_gw.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MERGED_GW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def next_gameweek(bootstrap: dict, override: int | None = None) -> dict:
    events = bootstrap["events"]
    if override is not None:
        match = [e for e in events if e["id"] == override]
        if not match:
            raise SystemExit(f"gameweek {override} not found")
        return match[0]
    upcoming = [e for e in events if not e["finished"]]
    if not upcoming:
        raise SystemExit("season is over -- no gameweek left to plan for")
    return upcoming[0]


def build_predictions(
    models: list, bootstrap: dict, fixtures: list[dict], gw: int,
    lookahead: int = LOOKAHEAD_GWS,
) -> pd.DataFrame:
    """
    Predictions for `gw` and the lookahead window, from live data.

    `models` is a list so callers can pass the single canonical model or an
    ensemble, exactly as simulate_season.build_predictions() does.
    """
    horizon = [g for g in range(gw, gw + lookahead) if g <= 38]
    extra = upcoming_rows(bootstrap, fixtures, horizon)
    rows = train_model.build_season_features(SEASON, PRIOR_SEASON, extra_rows=extra)
    rows["predicted_points"] = ensemble_predict(models, rows[train_model.FEATURE_COLUMNS])
    return rows


def unavailable_elements(bootstrap: dict) -> dict[int, str]:
    """
    Players the API says are *certainly* out: transferred/left the league, or
    injured/suspended with an explicit 0% chance of playing the next round.

    Deliberately certainty-only. plan.md attempts 5, 6 and 8 all showed that
    excluding players on a *probability* (<50%, <25%, or a proportional
    discount) is neutral-to-negative in the backtest, because the simulation's
    auto-sub and vice-captain fallback already act on the certain outcome for
    free. This filter is narrower than any of those: it only ever removes
    someone FPL has already declared a non-starter, and only from the buy pool.
    An owned player is never force-sold on this signal.
    """
    out = {}
    for element in bootstrap["elements"]:
        status, chance = element["status"], element["chance_of_playing_next_round"]
        if status == "u" or (status in ("i", "s") and chance == 0):
            out[element["id"]] = (element.get("news") or status).strip()
    return out


def my_team(session: requests.Session, manager_id: str) -> dict | None:
    """Authenticated view: real purchase/selling prices, free transfers and bank."""
    try:
        return fetch(f"my-team/{manager_id}/", session=session)
    except requests.HTTPError:
        return None


def public_squad(manager_id: str, gw: int) -> dict | None:
    """
    Unauthenticated fallback so a dry run still respects the current squad.
    Prices are live rather than what was paid, so the budget is approximate --
    fine for planning against, not for submitting on.
    """
    try:
        picks = fetch(f"entry/{manager_id}/event/{gw - 1}/picks/")
    except requests.HTTPError:
        return None
    return {"picks": picks.get("picks", []), "transfers": {}}


def plan_transfers(
    pool: pd.DataFrame, current: dict, free_transfers: int, bank: int
) -> tuple[pd.DataFrame, int, int]:
    """
    Mirrors simulate_season's weekly search: try k changes, keep whichever is
    best net of -4 per hit, and only move at all if it beats holding by
    TRANSFER_MARGIN. Capped at MAX_AUTOMATED_HITS so a bug cannot spend twenty
    points unattended.
    """
    owned = {p["element"] for p in current["picks"]}
    selling = {
        p["element"]: p.get("selling_price") or p.get("now_cost")
        for p in current["picks"]
    }
    live = dict(zip(pool["element"], pool["value"]))
    budget = sum(v if v is not None else live.get(e, 0) for e, v in selling.items()) + bank

    priced = pool.copy()
    priced["cost"] = [
        selling[e] if e in selling and selling[e] is not None else v
        for e, v in zip(priced["element"], priced["value"])
    ]

    # Taken from `priced`, not `pool`, so the held squad carries the same `cost`
    # basis as a transferred one and can be budget-checked the same way.
    held = priced[priced["element"].isin(owned)]
    hold_score = held["predicted_points"].sum()
    best_k, best_net, best_squad = 0, hold_score, held.copy()

    for k in range(1, min(free_transfers + MAX_AUTOMATED_HITS, 5) + 1):
        candidate = select_squad(
            priced, budget=budget, current_ids=owned, max_changes=k, cost_col="cost"
        )
        if candidate is None:
            continue
        net = candidate["predicted_points"].sum() - 4 * max(0, k - free_transfers)
        if net > best_net:
            best_k, best_net, best_squad = k, net, candidate

    if best_k and best_net <= hold_score + TRANSFER_MARGIN:
        return held.copy(), 0, budget
    return best_squad, best_k, budget


def choose_team(
    predictions: pd.DataFrame, gw: int, models: list,
    current: dict | None = None, free_transfers: int = 1, bank: int = 0,
    unavailable: dict[int, str] | None = None, lookahead: int = LOOKAHEAD_GWS,
    unlimited_transfers: bool = False,
) -> dict:
    gw_pool = predictions[predictions["GW"] == gw].copy()
    if gw_pool.empty:
        raise SystemExit(f"no players with a fixture in GW{gw}")

    horizon = build_horizon_scores(models, predictions, gw, lookahead)
    pool = gw_pool.copy()
    pool["predicted_points"] = pool["element"].map(horizon).fillna(pool["predicted_points"])

    # Only ever narrows what may be *bought*. Anyone already owned stays in the
    # pool so the squad can still be scored and fielded around them.
    owned_ids = {p["element"] for p in current["picks"]} if current else set()
    buyable = pool
    if unavailable:
        blocked = set(unavailable) - owned_ids
        buyable = pool[~pool["element"].isin(blocked)]

    # Before the GW1 deadline FPL lets you rewrite the whole squad for free, so
    # an auto-picked starting team is not something to transfer away one player
    # at a time -- plan_transfers() would cap out at a handful of changes and a
    # -4 hit that isn't actually charged. Treat it as the full build it is.
    if current is not None and unlimited_transfers:
        squad = select_squad(buyable, budget=STARTING_BUDGET)
        if squad is None:
            raise SystemExit("no legal squad found within budget")
        changed = len(set(squad["element"]) - {p["element"] for p in current["picks"]})
        return _finish(gw_pool, squad, changed, STARTING_BUDGET, free_transfers=changed)

    if current is None:
        squad, transfers, budget = select_squad(buyable, budget=STARTING_BUDGET), 0, STARTING_BUDGET
        if squad is None:
            raise SystemExit("no legal squad found within budget")
    else:
        owned = {p["element"] for p in current["picks"]}
        blanked = owned - set(gw_pool["element"])
        if blanked:
            raise SystemExit(
                f"{len(blanked)} owned player(s) have no GW{gw} fixture (blank gameweek). "
                "Handling blanks needs the fixture-aware grid; refusing to act on a "
                "partial squad."
            )
        squad, transfers, budget = plan_transfers(buyable, current, free_transfers, bank)

    return _finish(gw_pool, squad, transfers, budget, free_transfers)


def _finish(
    gw_pool: pd.DataFrame, squad: pd.DataFrame, transfers: int, budget: int,
    free_transfers: int,
) -> dict:
    # Starting XI and captaincy use this week's prediction, not the horizon --
    # you always want the best lineup for the week actually being played.
    resolved = gw_pool[gw_pool["element"].isin(set(squad["element"]))]
    xi, bench = pick_starting_xi(resolved)
    captain, vice = pick_captains(xi)
    return {
        "squad": squad, "xi": xi, "bench": bench,
        "captain": captain, "vice": vice, "transfers": transfers,
        "hits": max(0, transfers - free_transfers), "budget": budget,
    }


def validate(squad: pd.DataFrame, budget: int) -> list[str]:
    """Checks FantasyRules.md constraints before anything is submitted."""
    problems = []
    if len(squad) != 15:
        problems.append(f"squad has {len(squad)} players, expected 15")
    counts = squad["position_label"].value_counts().to_dict()
    for position, required in [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]:
        if counts.get(position, 0) != required:
            problems.append(f"{position}: {counts.get(position, 0)}, expected {required}")
    # Priced at sell value once a squad exists, since a rise is only half
    # refunded -- checking live market value against the budget would fail a
    # perfectly legal squad as soon as team value grew.
    cost_col = "cost" if "cost" in squad.columns else "value"
    cost = int(squad[cost_col].sum())
    if cost > budget:
        problems.append(f"squad costs {cost / 10:.1f}m, over budget {budget / 10:.1f}m")
    over = squad["team"].value_counts()
    for team, n in over[over > 3].items():
        problems.append(f"{n} players from {team}, max 3")
    return problems


def describe(choice: dict, gw: int, deadline: str) -> str:
    hits = choice["hits"]
    lines = [
        f"Gameweek {gw}  (deadline {deadline})",
        f"Squad value: {choice['squad']['value'].sum() / 10:.1f}m",
        f"Transfers: {choice['transfers']}"
        + (f"  ({hits} hit{'s' if hits != 1 else ''}, -{hits * 4} pts)" if hits else ""),
        "",
        "Starting XI:",
    ]
    for position in ["GKP", "DEF", "MID", "FWD"]:
        for _, row in choice["xi"][choice["xi"]["position_label"] == position].iterrows():
            mark = ""
            if row["element"] == choice["captain"]:
                mark = "  (C)"
            elif row["element"] == choice["vice"]:
                mark = "  (V)"
            lines.append(
                f"  {position}  {row['name']:<28} {row['value'] / 10:>5.1f}m"
                f"  pred {row['predicted_points']:>5.2f}{mark}"
            )
    lines.append("")
    lines.append("Bench:")
    for _, row in choice["bench"].iterrows():
        lines.append(
            f"  {row['position_label']}  {row['name']:<28} {row['value'] / 10:>5.1f}m"
            f"  pred {row['predicted_points']:>5.2f}"
        )
    return "\n".join(lines)


def log_decision(payload: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def login() -> requests.Session:
    """
    Authenticates using a session cookie copied from a signed-in browser.

    The email/password flow this used to do is gone. It posted to
    users.premierleague.com/accounts/login/, and that host no longer resolves at
    all -- `Resolve-DnsName users.premierleague.com` returns NXDOMAIN, so every
    --apply run has failed at DNS since the Premier League retired it. They have
    moved to an OAuth2/OIDC flow on account.premierleague.com behind Ping
    Identity: /as/authorize demands a client_id and redirects to a JavaScript
    sign-on widget, and the site returns 403 to non-browser clients.

    Driving that flow from a script would mean defeating bot protection, which
    is not something this project should do. Reusing a session you opened
    yourself, in your own browser, for your own team, needs none of that.

    To get the cookie: sign in at fantasy.premierleague.com, open devtools ->
    Network, click any request to /api/, and copy the whole `Cookie:` request
    header. Put it in .env as FPL_COOKIE. It is long, it belongs on one line,
    and it expires -- when a run reports being signed out, copy a fresh one.
    """
    cookie = os.environ.get("FPL_COOKIE", "").strip()
    if not cookie:
        raise SystemExit(
            "FPL_COOKIE is not set.\n"
            "  FPL_EMAIL/FPL_PASSWORD no longer work: the login host they used\n"
            "  (users.premierleague.com) has been retired and does not resolve.\n"
            "  Sign in at fantasy.premierleague.com, open devtools -> Network,\n"
            "  click any /api/ request, copy the entire 'Cookie:' request header,\n"
            "  and set it as FPL_COOKIE in .env (one line)."
        )

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Cookie": cookie,
        "Referer": "https://fantasy.premierleague.com/",
    })

    # The API is Django REST Framework, so a session-authenticated POST is
    # rejected with 403 unless X-CSRFToken echoes the csrftoken cookie. GETs are
    # unaffected, which is why a cookie can look perfectly valid under
    # --check-auth and still fail the moment anything is submitted.
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "csrftoken" and value:
            session.headers["X-CSRFToken"] = value
            break
    else:
        print("[warn] no csrftoken in FPL_COOKIE -- reads will work, but any "
              "submit will likely be rejected with 403. Re-copy the header from "
              "a request made while signed in.")
    return session


def apply_transfers(
    session: requests.Session, manager_id: str, gw: int, current: dict,
    squad: pd.DataFrame, positions: dict[int, str],
) -> int:
    """
    Buys and sells so the real team matches the chosen squad. Must run before
    the lineup is set -- /my-team/ only orders players you already own, so
    submitting picks without this would try to field players who were never
    bought.
    """
    owned = {p["element"] for p in current["picks"]}
    chosen = set(squad["element"])
    if owned == chosen:
        return 0

    selling = {p["element"]: p.get("selling_price") for p in current["picks"]}
    buying = dict(zip(squad["element"], squad["value"]))
    out_by_position: dict[str, list[int]] = {}
    for element in owned - chosen:
        out_by_position.setdefault(positions[element], []).append(element)

    # Paired by position: select_squad always returns the same 2/5/5/3 shape, so
    # the sets match up exactly and FPL sees a like-for-like swap.
    transfers = []
    for element_in in sorted(chosen - owned):
        position = positions[element_in]
        if not out_by_position.get(position):
            raise SystemExit(f"no {position} to sell for incoming {element_in}")
        element_out = out_by_position[position].pop()
        transfers.append({
            "element_in": int(element_in),
            "element_out": int(element_out),
            "purchase_price": int(buying[element_in]),
            "selling_price": int(selling.get(element_out) or buying.get(element_out, 0)),
        })

    response = session.post(
        f"{API}/transfers/",
        json={"chip": None, "entry": int(manager_id), "event": gw, "transfers": transfers},
        headers={"Content-Type": "application/json",
                 "Referer": "https://fantasy.premierleague.com/transfers"},
        timeout=60,
    )
    response.raise_for_status()
    print(f"[ok] made {len(transfers)} transfer(s)")
    return len(transfers)


def submit(
    session: requests.Session, manager_id: str, gw: int, choice: dict,
    current: dict | None = None, positions: dict[int, str] | None = None,
) -> None:
    if current is not None:
        apply_transfers(session, manager_id, gw, current, choice["squad"], positions or {})

    picks = []
    ordered = list(choice["xi"].itertuples()) + list(choice["bench"].itertuples())
    for slot, row in enumerate(ordered, start=1):
        picks.append({
            "element": int(row.element),
            "position": slot,
            "is_captain": int(row.element) == int(choice["captain"]),
            "is_vice_captain": int(row.element) == int(choice["vice"]),
        })
    response = session.post(
        f"{API}/my-team/{manager_id}/",
        json={"chip": None, "picks": picks},
        headers={"Content-Type": "application/json",
                 "Referer": "https://fantasy.premierleague.com/my-team"},
        timeout=60,
    )
    response.raise_for_status()
    print(f"[ok] submitted GW{gw} team to entry {manager_id}")


def check_auth(manager_id: str) -> None:
    """Logs in and prints the squad FPL currently holds. Submits nothing."""
    session = login()

    team = my_team(session, manager_id)
    if not team or not team.get("picks"):
        raise SystemExit(
            f"[fail] /my-team/{manager_id}/ returned no squad. Either the cookie has\n"
            "  expired (copy a fresh one from a signed-in browser), or\n"
            "  FPL_MANAGER_ID belongs to a different account than the cookie does."
        )
    print("[ok] cookie is valid and authorises this entry")

    picks = team["picks"]
    transfers = team.get("transfers") or {}
    bootstrap = fetch("bootstrap-static/")
    meta = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    print(f"[ok] entry {manager_id} holds {len(picks)} players, "
          f"bank {(transfers.get('bank') or 0) / 10:.1f}m, "
          f"{transfers.get('limit') or 0} free transfer(s)")
    spend = 0
    for pick in picks:
        element = meta[pick["element"]]
        spend += pick.get("selling_price") or element["now_cost"]
        print(f"  {POSITIONS[element['element_type']]:<4}{element['web_name']:<22}"
              f"{teams[element['team']]:<5}{element['now_cost'] / 10:>5.1f}m")
    print(f"  squad value {spend / 10:.1f}m")
    print("\nNothing was submitted. Re-run with --apply to replace this squad.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="submit the team; without this the run is a dry run")
    parser.add_argument("--gw", type=int, help="target a specific gameweek")
    parser.add_argument("--free-transfers", type=int, default=1,
                        help="free transfers available, when not readable from the API")
    parser.add_argument("--only-if-due", action="store_true",
                        help="exit quietly unless the next deadline is inside the run "
                             "window, so a plain hourly cron entry can do the scheduling")
    parser.add_argument("--lookahead", type=int, default=LOOKAHEAD_GWS,
                        help=f"gameweeks the transfer/squad valuation looks ahead "
                             f"(default {LOOKAHEAD_GWS})")
    parser.add_argument("--no-availability-filter", action="store_true",
                        help="buy players FPL has already declared out (off by default)")
    parser.add_argument("--check-auth", action="store_true",
                        help="log in, print the squad FPL currently holds, and exit "
                             "without training or submitting anything")
    args = parser.parse_args()

    # A .env file is documented (and gitignored) as the credential workflow,
    # but nothing previously loaded it -- os.environ alone only sees vars a
    # shell or cron entry actually exported. load_dotenv() is a no-op if no
    # .env file exists, so this doesn't change behavior for anyone already
    # exporting real environment variables.
    load_dotenv()

    # Checked before any network work, so a missing credential fails in a second
    # rather than after a model train.
    manager_id = os.environ.get("FPL_MANAGER_ID")
    needs_credentials = args.apply or args.check_auth
    flag = "--apply" if args.apply else "--check-auth"
    if needs_credentials and not manager_id:
        raise SystemExit(f"{flag} needs FPL_MANAGER_ID in the environment")
    if needs_credentials and not os.environ.get("FPL_COOKIE", "").strip():
        raise SystemExit(f"{flag} needs FPL_COOKIE in the environment -- see login()")

    # There is otherwise no way to test the credential path short of --apply,
    # which submits a real team. Everything up to and including reading the
    # squad is the same code --apply runs; it just stops before deciding
    # anything, so a login or manager-id problem surfaces harmlessly.
    if args.check_auth:
        check_auth(manager_id)
        return

    print("Fetching live FPL data...")
    bootstrap = fetch("bootstrap-static/")
    fixtures = fetch("fixtures/")

    event = next_gameweek(bootstrap, args.gw)
    gw = event["id"]
    deadline = datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    print(f"Target: GW{gw}, deadline {deadline.isoformat()} "
          f"({(deadline - now).total_seconds() / 3600:.1f}h away)")

    # Self-gating beats scheduling one job per deadline: a single hourly cron
    # entry covers a whole season of irregular kickoff times, and a missed hour
    # is retried automatically rather than skipping that gameweek entirely.
    if args.only_if_due:
        opens = deadline - DEADLINE_LEAD - timedelta(minutes=30)
        if not opens <= now <= deadline:
            print(f"Not due yet (window opens {opens.isoformat()}). Exiting.")
            return

    if args.apply and now > deadline:
        raise SystemExit(f"deadline for GW{gw} has passed -- refusing to submit")
    if args.apply and now < deadline - EARLY_RUN_WARNING:
        print(f"[warn] running {(deadline - now).total_seconds() / 3600:.0f}h before the "
              f"deadline; team news, prices and injuries may still change")

    finished = [e["id"] for e in bootstrap["events"] if e["finished"]]
    print(f"Syncing {SEASON} ({len(finished)} finished gameweeks) -> {DATA_DIR / SEASON}")
    sync_season(bootstrap, fixtures, finished)

    session = login() if args.apply else None

    current, free_transfers, bank = None, args.free_transfers, 0
    if manager_id:
        authenticated = my_team(session, manager_id) if session else None
        if authenticated and authenticated.get("picks"):
            current = authenticated
            transfers = authenticated.get("transfers") or {}
            free_transfers = transfers.get("limit") or args.free_transfers
            bank = transfers.get("bank") or 0
            print(f"Current squad: {len(current['picks'])} players, "
                  f"{free_transfers} free transfer(s), bank {bank / 10:.1f}m")
        else:
            current = public_squad(manager_id, gw)
            if current and current["picks"]:
                print(f"Current squad from public picks: {len(current['picks'])} players "
                      f"(prices approximate, assuming {free_transfers} free transfer(s))")
            else:
                current = None
    if current is None:
        print("No existing squad found -- building an initial 15 from scratch.")

    # Nothing has been played yet, so any existing squad is a provisional pick
    # (FPL auto-fills one on signup) that can be rewritten in full for free.
    unlimited = current is not None and not finished
    if unlimited:
        print("Before the GW1 deadline -- unlimited free transfers, so the "
              "existing squad is rebuilt from scratch rather than nudged.")

    train_model.TRAIN_SEASONS = LIVE_TRAIN_SEASONS
    # A full 15-player build is the decision plan.md Phase 4 traced this
    # pipeline's worst instability to: hundreds of near-tied squads separated by
    # sub-1-point margins, where one model's idiosyncratic wobble on one player
    # tips the whole season onto a different, compounding path (a 0.74-point
    # difference on a single player moved a season total by over 100 points).
    # simulate_season already answers that with an ensemble at exactly these two
    # decision points; live was still building GW1 off a single model.
    full_rebuild = current is None or unlimited
    if full_rebuild:
        print(f"Training {1 + len(ENSEMBLE_EXTRA_SEEDS)}-model ensemble on "
              f"{LIVE_TRAIN_SEASONS[0]} -> {LIVE_TRAIN_SEASONS[-1]} (full squad build)...")
        models = [train_model.train_baseline_model()] + [
            train_model.train_baseline_model(seed=s) for s in ENSEMBLE_EXTRA_SEEDS
        ]
    else:
        print(f"Training model on {LIVE_TRAIN_SEASONS[0]} -> {LIVE_TRAIN_SEASONS[-1]}...")
        models = [train_model.train_baseline_model()]

    unavailable = {} if args.no_availability_filter else unavailable_elements(bootstrap)
    if unavailable:
        print(f"{len(unavailable)} player(s) flagged unavailable by the API and "
              f"excluded from the buy pool (owned players are never force-sold).")

    print(f"Building live predictions (lookahead {args.lookahead} GWs)...")
    predictions = build_predictions(models, bootstrap, fixtures, gw, args.lookahead)
    choice = choose_team(predictions, gw, models, current, free_transfers, bank,
                         unavailable, args.lookahead, unlimited)

    problems = validate(choice["squad"], choice["budget"])
    print()
    print(describe(choice, gw, deadline.isoformat()))
    print()

    log_decision({
        "run_at": now, "gw": gw, "deadline": deadline, "applied": bool(args.apply),
        "squad": [int(e) for e in choice["squad"]["element"]],
        "captain": int(choice["captain"]), "vice": int(choice["vice"]),
        "transfers": choice["transfers"], "hits": choice["hits"],
        "problems": problems,
        # Provenance: which of the two model paths ran, how far the valuation
        # looked, and exactly who was withheld from the buy pool and why -- so a
        # squad can be explained after the fact without re-running anything.
        "models": len(models), "lookahead": args.lookahead,
        "excluded": {int(e): why for e, why in sorted(unavailable.items())},
    })

    if problems:
        for problem in problems:
            print(f"[fail] {problem}")
        raise SystemExit("squad violates FantasyRules.md -- refusing to continue")
    print("[ok] squad satisfies every FantasyRules.md constraint")

    if choice["hits"] > MAX_AUTOMATED_HITS:
        raise SystemExit(
            f"plan takes {choice['hits']} hits, above the automated ceiling of "
            f"{MAX_AUTOMATED_HITS} -- refusing to submit unattended"
        )

    if not args.apply:
        print("\nDry run. Nothing was submitted. Re-run with --apply to submit.")
        return

    positions = {e["id"]: POSITIONS[e["element_type"]] for e in bootstrap["elements"]}
    submit(session, manager_id, gw, choice, current, positions)


if __name__ == "__main__":
    main()
