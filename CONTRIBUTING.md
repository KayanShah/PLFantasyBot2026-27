# Contributing to PLFantasyBot

Thanks for taking an interest in this project. It's a hobby build, but the bar for changes is real — the codebase has already been through several rounds of "this seemed like a good idea and turned out not to be," and the whole point of the discipline below is to catch that honestly instead of just keeping whatever number looks best.

> [!IMPORTANT]
> **Read [`plan.md`](plan.md) before proposing anything.** It documents every experiment tried so far — including a dozen reverted ones, each with the exact numbers and the reasoning for why it didn't work. If your idea overlaps with something already tried and reverted there, it'll save you the work of re-discovering the same result. If it doesn't overlap, `plan.md` is still the best map of what's already validated vs. still open.

## How to contribute

Two ways, pick whichever suits you:

1. **Fork the repo and open a pull request**, with every change clearly documented — what you changed, why, and the evidence it actually helps (see [Validating a change](#validating-a-change) below). PRs without that evidence attached won't be merged, regardless of how the code looks.
2. **Email [hi@kayanshah.com](mailto:hi@kayanshah.com)** if you'd rather discuss an idea first, report a bug, or don't want to go through the PR process yourself.

## Validating a change

This is the part that actually matters. A change that "looks right" or "should obviously help" has repeatedly turned out not to, once measured — see `plan.md`'s long list of reverted experiments for concrete examples (richer stat features, dynamic chip timing, various uses of injury data, and more). So:

- **Never trust a single season's result.** Run [`model/multi_season_backtest.py`](model/multi_season_backtest.py), which tests a change across three independent seasons (2023-24, 2024-25, 2025-26), each trained only on seasons strictly before it — no leakage. A change is only worth keeping if it's better-or-equal across *most* of the three.
- **Check your result against the noise floor, not against zero.** A placebo test (changing the model's random seed and nothing else) showed the season score moves by roughly **±15 to ±35 points** on its own, from nothing meaningful at all. A swing smaller than that isn't evidence of anything — see `plan.md` Phase 4 for the full noise-floor writeup and how it was measured.
- **Change one thing at a time.** Every bundled experiment in this project's history (multiple features + chip-timing changes together, for example) ended up impossible to attribute cleanly when the result was mixed. Isolate the change you're proposing so its effect can actually be measured.
- **Watch for leakage.** Every feature in the model is built from data that would genuinely have been known *before* the gameweek it's predicting — rolling stats are shifted so a gameweek's features never include its own outcome, fixture/difficulty data is used because it's public in advance, and so on. If your change uses information a real manager wouldn't have had before that gameweek's deadline, it's not a fair test.
- **Report the honest result, including if it's negative.** Several entries in `plan.md` are "tried this, it made things worse, here's why" — that's treated as a genuinely useful outcome in this project, not a failure to hide. A PR that reverts its own change after measuring it, with the finding documented, is a welcome contribution.

## Code style

- No comments explaining *what* code does — names should already make that clear. A comment is only worth adding when it explains a non-obvious *why* (a constraint, a workaround, a subtlety that would surprise a reader).
- Keep changes scoped to what's needed. Don't refactor unrelated code, add abstractions for hypothetical future needs, or add error handling for scenarios that can't happen here.
- Match the existing structure: data fetching lives in `scrapers/` and `model/fetch_*.py`, model training in `model/train_model.py`, the optimizer in `model/optimizer.py`, and the season simulation in `model/simulate_season.py`. See [`README.md`](README.md#repo-layout) for the full layout.

## Setup

```bash
pip install -r requirements.txt
```

See [`README.md`](README.md) for how to run each part of the pipeline (scrapers, model training, the season simulator, the website, the multi-season backtest).

## Questions

Open an issue, or email [hi@kayanshah.com](mailto:hi@kayanshah.com).
