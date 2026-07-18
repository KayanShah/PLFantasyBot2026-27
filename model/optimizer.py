"""
Squad selection and starting-XI logic, enforcing the constraints in
FantasyRules.md: 15-man squad (2 GKP/5 DEF/5 MID/3 FWD), £100.0m budget
(stored in tenths, matching the FPL API), max 3 players per club, and
valid starting-XI formations.
"""

import numpy as np
import pandas as pd
from scipy.optimize import LinearConstraint, milp

POSITION_REQUIREMENTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
SQUAD_SIZE = 15

VALID_FORMATIONS = [  # (DEF, MID, FWD), GK is always 1
    (3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2),
    (4, 5, 1), (5, 3, 2), (5, 4, 1),
]


def select_squad(
    pool: pd.DataFrame,
    budget: int,
    current_ids: set[int] | None = None,
    max_changes: int | None = None,
    cost_col: str = "value",
) -> pd.DataFrame | None:
    """
    Picks 15 players maximizing total predicted_points, subject to budget,
    position, and club-limit constraints.

    If current_ids + max_changes are given, at most max_changes players may
    differ from the current squad (used to model limited weekly transfers).
    `cost_col` lets the caller price retained players at their FPL sell value
    (which can be less than their live market value) rather than the live
    buy price everyone else in the pool is priced at.
    Returns None if no legal squad exists under the constraints.
    """
    pool = pool.reset_index(drop=True)
    n = len(pool)
    c = -pool["predicted_points"].to_numpy()

    constraints = [
        LinearConstraint(np.ones((1, n)), SQUAD_SIZE, SQUAD_SIZE),
        LinearConstraint(pool[cost_col].to_numpy().reshape(1, n), -np.inf, budget),
    ]
    for pos, count in POSITION_REQUIREMENTS.items():
        mask = (pool["position_label"] == pos).astype(float).to_numpy().reshape(1, n)
        constraints.append(LinearConstraint(mask, count, count))
    for team in pool["team"].unique():
        mask = (pool["team"] == team).astype(float).to_numpy().reshape(1, n)
        constraints.append(LinearConstraint(mask, 0, MAX_PER_CLUB))

    if current_ids is not None and max_changes is not None:
        in_current = pool["element"].isin(current_ids).astype(float).to_numpy().reshape(1, n)
        constraints.append(LinearConstraint(in_current, SQUAD_SIZE - max_changes, SQUAD_SIZE))

    result = milp(c, integrality=np.ones(n), bounds=(0, 1), constraints=constraints)
    if not result.success:
        return None
    return pool.iloc[np.round(result.x) == 1].copy()


def pick_starting_xi(squad: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (starting_xi, bench) maximizing predicted_points over valid formations."""
    gk = squad[squad["position_label"] == "GKP"].sort_values("predicted_points", ascending=False)
    outfield = squad[squad["position_label"] != "GKP"]

    best_xi, best_score = None, -np.inf
    for defs, mids, fwds in VALID_FORMATIONS:
        picks = [gk.iloc[[0]]]
        for pos, count in [("DEF", defs), ("MID", mids), ("FWD", fwds)]:
            picks.append(
                outfield[outfield["position_label"] == pos]
                .sort_values("predicted_points", ascending=False)
                .head(count)
            )
        xi = pd.concat(picks)
        score = xi["predicted_points"].sum()
        if score > best_score:
            best_xi, best_score = xi, score

    bench = squad[~squad["element"].isin(best_xi["element"])].copy()
    # Bench order: reserve GK last, outfield subs ordered by predicted points.
    bench_gk = bench[bench["position_label"] == "GKP"]
    bench_outfield = bench[bench["position_label"] != "GKP"].sort_values(
        "predicted_points", ascending=False
    )
    bench = pd.concat([bench_outfield, bench_gk])
    return best_xi, bench


def pick_captains(starting_xi: pd.DataFrame) -> tuple[int, int]:
    ranked = starting_xi.sort_values("predicted_points", ascending=False)
    return ranked.iloc[0]["element"], ranked.iloc[1]["element"]
