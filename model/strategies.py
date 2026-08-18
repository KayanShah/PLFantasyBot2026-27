"""
Named parameter sets ("strategies") for squad-construction/transfer policy.

Every strategy runs through the exact same validated engine -- same trained
model, same feature set, same ILP optimizer, same chip logic, same sell-value
rules (simulate_season.py / live_pipeline.py). Only the knobs below differ:
how many -4 hits a week is willing to take, how far ahead it plans fixtures,
and how strongly it tilts toward high-ownership "safe" picks vs low-ownership
differentials. Nothing about the model or its features changes between them,
so differences in outcome are attributable to the policy, not to four
separately-tuned pipelines.

`order` controls display order on the website -- Differential is deliberately
last (highest number), per its own billing as the high-risk option to look at
after, not instead of, the validated build.
"""

STRATEGIES = {
    "balanced": {
        "label": "Balanced",
        "short": "The flagship build.",
        "description": (
            "5-gameweek fixture lookahead, at most one -4 hit per week, "
            "TRANSFER_MARGIN and hit ceiling both tuned via a 5-seed x "
            "3-season backtest sweep (see plan.md). This is the only "
            "strategy with a season's worth of validated backtesting behind "
            "it, and the only one ever applied to the real FPL team."
        ),
        "risk": "Validated",
        "risk_tier": "low",
        "order": 0,
        "transfer_margin": 1.5,
        "max_hits_per_gw": 1,
        "lookahead_gws": 5,
        "differential_weight": 0.0,
    },
    "conservative": {
        "label": "Conservative",
        "short": "Never takes a hit.",
        "description": (
            "Same model and lookahead as Balanced, but will never spend a -4 "
            "-- only ever uses free transfers, banking them when nothing "
            "clears the bar. Trades some upside for zero hit risk."
        ),
        "risk": "Low risk",
        "risk_tier": "low",
        "order": 1,
        "transfer_margin": 1.5,
        "max_hits_per_gw": 0,
        "lookahead_gws": 5,
        "differential_weight": 0.0,
    },
    "reactive": {
        "label": "Reactive",
        "short": "No fixture lookahead.",
        "description": (
            "Picks purely on next-gameweek form -- no fixture-run planning "
            "at all (lookahead=1 vs Balanced's 5). Exists to show, live, "
            "whether the lookahead Balanced relies on is actually earning "
            "its keep."
        ),
        "risk": "Experimental",
        "risk_tier": "medium",
        "order": 2,
        "transfer_margin": 1.5,
        "max_hits_per_gw": 1,
        "lookahead_gws": 1,
        "differential_weight": 0.0,
    },
    "differential": {
        "label": "Differential",
        "short": "High risk, high reward.",
        "description": (
            "Deliberately chases low-ownership, high-ceiling picks over safe "
            "template players (up to 1.6x the raw predicted score for a "
            "0%-owned player), and will take up to two -4 hits a week "
            "chasing marginal transfers most managers wouldn't bother with. "
            "Built to swing hard, not to be steady -- expect bigger weeks "
            "and bigger busts than the other three. Never applied to a real "
            "team automatically."
        ),
        "risk": "High risk / high reward",
        "risk_tier": "high",
        "order": 3,
        "transfer_margin": 0.5,
        "max_hits_per_gw": 2,
        "lookahead_gws": 5,
        "differential_weight": 0.6,
    },
}


def ordered() -> list[tuple[str, dict]]:
    return sorted(STRATEGIES.items(), key=lambda kv: kv[1]["order"])
