# real_team screenshots

Source evidence for `data/live_squads_real_team.json`'s GW1-5 history — real
screenshots of the actual FPL app squad, gameweek by gameweek, used to
correct that file when it had drifted from what was actually held (see
`plan.md`). Each gameweek's raw player points (captain doubling backed out)
were verified to sum to exactly the screenshot's own displayed Total Pts
before any correction was written.

| File | Gameweek | Total Pts shown (gross) | Real net score (hits deducted) |
|---|---|---|---|
| `gw1.jpg` | 1 | 31 | 31 |
| `gw2.jpg` | 2 | 76 | 76 |
| `gw3.jpg` | 3 | 36 | 28 (2 hits, -8) |
| `gw4.jpg` | 4 | 73 | 69 (1 hit, -4) |
| `gw5.jpg` | 5 | 65 | 61 (1 hit, -4) |

**"Total Pts shown" is gross, not net of hits.** This screen's own tile
never subtracts a transfer-hit cost, even on a gameweek where hits were
actually taken -- confirmed the hard way: summing this screen's displayed
player points always equals its own "Total Pts" tile regardless of hits,
so that match is not evidence hits weren't taken. The real net season
total (265 after GW5) only ever came from checking the official app
directly, not from anything on this screen. Hit counts above are inferred
from standard free-transfer accrual (1/week, cap 5) given each gameweek's
real transfer count, then confirmed by summing to exactly 265.
