# Fantasy Premier League — Official Rules (2025/26)

Reference doc for the rules the bot's optimizer needs to encode. Sourced from the official Premier League FPL rules pages — see [Sources](#sources). Re-check each season, as rules (especially chips and transfers) have changed year to year.

---

## 1. Squad Selection

- **Budget:** £100.0m to build an initial 15-player squad.
- **Squad composition (15 players):**
  - 2 Goalkeepers
  - 5 Defenders
  - 5 Midfielders
  - 3 Forwards
- **Club limit:** Maximum of 3 players from any single Premier League club.

## 2. Starting XI & Formation

- Only your **starting 11** score points each gameweek; the other 4 sit on the bench.
- Valid formations (GK–DEF–MID–FWD): `3-4-3`, `3-5-2`, `4-3-3`, `4-4-2`, `4-5-1`, `5-3-2`, `5-4-1`.
- Any formation is legal as long as it includes: 1 goalkeeper, at least 3 defenders, at least 2 midfielders, at least 1 forward.
- **Captain:** scores 2x points. **Vice-captain:** automatically becomes captain (2x) if the captain doesn't play at all that gameweek.
- **Automatic substitutions:** if a starter doesn't play, they're automatically replaced by the highest-priority bench player who did play, respecting formation validity.

## 3. Scoring System

### Points by position

| Action | GK | DEF | MID | FWD |
|---|---|---|---|---|
| Goal scored | 10 | 6 | 5 | 4 |
| Clean sheet (60+ mins) | 4 | 4 | 1 | 0 |

### Universal actions (all positions)

| Action | Points |
|---|---|
| Assist | +3 |
| Playing up to 60 minutes | +1 |
| Playing 60+ minutes | +2 |
| Penalty save (GK) | +5 |
| Every 3 shots saved (GK) | +1 |
| Penalty miss | -2 |
| Yellow card | -1 |
| Red card | -3 |
| Own goal | -2 |
| Every 2 goals conceded (GK/DEF) | -1 |
| Bonus points (top 3 performers by BPS in a match) | +3 / +2 / +1 |

### Defensive Contributions (new for 2025/26)

Rewards outfield defensive work outside the penalty area, based on a combined tally of clearances, blocks, interceptions, tackles, and (for midfielders/forwards) ball recoveries:

| Position | Threshold | Points |
|---|---|---|
| Defender | 10+ combined CBIT (clearances, blocks, interceptions, tackles) in a match | +2 |
| Midfielder / Forward | 12+ combined CBIRT (as above, plus ball recoveries) in a match | +2 |

## 4. Transfers

- **Free transfers:** 1 per gameweek once the season's first deadline has passed.
- **Rollover:** unused free transfers carry over, up to a maximum stockpile of **5**.
- **Extra transfers:** any transfer beyond your free allowance costs **-4 points** each ("taking a hit").
- **AFCON top-up:** free transfers are topped up to the maximum of 5 at Gameweek 16, to account for players leaving for the Africa Cup of Nations mid-season.
- **Deadlines:** all transfers/team changes must be made before each gameweek's deadline (typically ~90 minutes before the first kickoff of that gameweek).

## 5. Chips

For 2025/26, managers get **two full sets** of chips — one set for the first half of the season (usable up to the Gameweek 19 deadline, ~30 Dec), one for the second half. The Assistant Manager chip (introduced 2024/25) has been removed.

| Chip | Effect |
|---|---|
| **Wildcard** | Unlimited free transfers for that gameweek, with no effect on future free transfers. |
| **Free Hit** | Unlimited free transfers for one gameweek only — squad automatically reverts to its previous state the following gameweek. |
| **Bench Boost** | Points scored by all 4 bench players are added to your gameweek total. |
| **Triple Captain** | Captain scores 3x points instead of 2x, for that gameweek only. |

- Only **one chip** can be played per gameweek (excluding Wildcard interactions with the automatic-sub rules).
- Unused first-half chips **do not carry over** — use them or lose them by the Gameweek 19 deadline.

## 6. Bonus Points System (BPS)

- Every player is assigned a BPS score each match based on an algorithm weighing goals, assists, clean sheets, saves, defensive actions, and other contributions, alongside card/error deductions.
- The top 3 BPS scorers in each match receive bonus FPL points: **+3** (1st), **+2** (2nd), **+1** (3rd). Ties are shared per official tie-break rules.

## 7. Prices & Value

- Player prices fluctuate through the season based on transfer market activity (net transfers in/out), not performance directly.
- Squad value is the sum of each owned player's **current** price; selling a player that has risen in price nets only half the increase (the "sell-on fee"), rounded down.

## 8. Leagues

- **Classic leagues:** ranked by total season points.
- **Head-to-Head leagues:** weekly matchups scored like a league table (win/draw/loss) based on gameweek points.
- New for 2025/26: elite global leagues for the top 1% and top 10% of managers.

---

## Sources

- [FPL basics explained: Scoring points](https://www.premierleague.com/en/news/2174909)
- [FPL basics explained: How to pick a squad](https://www.premierleague.com/en/news/2174419/fpl-basics-how-to-pick-a-squad)
- [FPL basics explained: How to make transfers](https://www.premierleague.com/en/news/2174907)
- [FPL basics explained: How to use your chips](https://www.premierleague.com/en/news/2174900)
- [All you need to know about changes to Fantasy for 2025/26](https://www.premierleague.com/en/news/4362211/all-you-need-to-know-about-changes-to-fantasy-for-202526)
- [What's new for 2025/26: Changes in Fantasy Premier League](https://www.premierleague.com/en/news/4373187/whats-new-for-202526-changes-in-fantasy-premier-league)
- [FPL managers now have FIVE free transfers](https://www.premierleague.com/en/news/4461660/fpl-managers-to-have-five-free-transfers-on-saturday-all-you-need-to-know)
- [What's new in 2025/26 Fantasy: Two sets of chips](https://www.premierleague.com/en/news/4362027/whats-new-in-202526-fantasy-two-sets-of-chips)

**Note:** rules (especially transfer/chip mechanics and defensive contributions) have changed materially between recent seasons — re-verify against the official [FPL rules page](https://fantasy.premierleague.com/help/rules) at the start of each new season before hardcoding constraints into the optimizer.
