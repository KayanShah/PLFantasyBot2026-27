"""
Builds website/index.html — a self-contained, single-file site showing the
bot's team for every gameweek of its best 2025-26 season simulation, in a
pitch-view layout you can scroll/click through gameweek by gameweek.

Reads data/season_2025-26_squads.json (produced by model/simulate_season.py)
and embeds it directly into the page, so the result is a single HTML file
that works by just opening it in a browser — no server, no build step.
"""

import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "season_2025-26_squads.json"
OUT_PATH = Path(__file__).resolve().parent / "index.html"

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PLFantasyBot — 2025-26 Season</title>
<style>
  :root {
    --pitch-1: #1f8a45;
    --pitch-2: #22994c;
    --card-bg: #ffffff;
    --card-border: #d8dde3;
    --text-dark: #1a1a2e;
    --text-mid: #5a6472;
    --bench-bg: #eef1f4;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f4f6f8;
    color: var(--text-dark);
  }
  header {
    background: linear-gradient(90deg, #38003c, #5e0067);
    color: white;
    padding: 16px 20px;
    text-align: center;
  }
  header h1 { margin: 0 0 4px; font-size: 1.3rem; }
  header .subtitle { font-size: 0.85rem; opacity: 0.85; }

  .nav-bar {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    background: white;
    padding: 12px 16px;
    border-bottom: 1px solid var(--card-border);
    position: sticky;
    top: 0;
    z-index: 10;
    flex-wrap: wrap;
  }
  .nav-bar button {
    background: #37003c;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 0.9rem;
    cursor: pointer;
  }
  .nav-bar button:disabled { opacity: 0.35; cursor: default; }
  .nav-bar select {
    padding: 8px 10px;
    border-radius: 6px;
    border: 1px solid var(--card-border);
    font-size: 0.9rem;
  }
  .gw-stats {
    display: flex;
    gap: 18px;
    font-size: 0.85rem;
    color: var(--text-mid);
    flex-wrap: wrap;
    justify-content: center;
  }
  .gw-stats b { color: var(--text-dark); }
  .chip-badge {
    background: #ffd60a;
    color: #37003c;
    padding: 2px 10px;
    border-radius: 12px;
    font-weight: 700;
    font-size: 0.78rem;
  }

  .pitch {
    max-width: 900px;
    margin: 20px auto 0;
    background: linear-gradient(180deg, var(--pitch-1), var(--pitch-2));
    border-radius: 14px;
    padding: 22px 12px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.12);
  }
  .row {
    display: flex;
    justify-content: center;
    gap: 10px;
    margin-bottom: 18px;
    flex-wrap: wrap;
  }

  .player-card {
    width: 108px;
    background: var(--card-bg);
    border-radius: 8px;
    overflow: hidden;
    text-align: center;
    box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    position: relative;
  }
  .player-card .badge-row {
    position: absolute;
    top: 3px;
    right: 3px;
    display: flex;
    gap: 2px;
  }
  .player-card .armband {
    background: #1a1a2e;
    color: white;
    font-size: 0.62rem;
    font-weight: 800;
    border-radius: 50%;
    width: 16px;
    height: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .player-card .armband.tc { background: #ffd60a; color: #1a1a2e; }
  .player-card .kit {
    height: 34px;
    background: #37003c;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.03em;
  }
  .player-card .name {
    font-size: 0.72rem;
    font-weight: 700;
    padding: 4px 4px 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .player-card .opponent {
    font-size: 0.68rem;
    font-weight: 700;
    color: white;
    padding: 3px 4px;
  }
  .player-card .points {
    font-size: 0.68rem;
    color: var(--text-mid);
    padding: 2px 4px 4px;
  }
  .player-card .points.zero { opacity: 0.5; }

  .diff-1 { background: #04a777; }
  .diff-2 { background: #4fd68a; color: #1a1a2e; }
  .diff-3 { background: #8a8f98; }
  .diff-4 { background: #e8722c; }
  .diff-5 { background: #d0356b; }
  .diff-none { background: #8a8f98; }
  .diff-2 .opponent { color: #1a1a2e; }

  .bench-wrap {
    max-width: 900px;
    margin: 0 auto 30px;
    background: var(--bench-bg);
    border-radius: 12px;
    padding: 14px 12px 18px;
  }
  .bench-label {
    text-align: center;
    font-size: 0.75rem;
    font-weight: 700;
    color: var(--text-mid);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 10px;
  }
  .bench-row {
    display: flex;
    justify-content: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .legend {
    max-width: 900px;
    margin: 0 auto 30px;
    display: flex;
    justify-content: center;
    gap: 14px;
    flex-wrap: wrap;
    font-size: 0.75rem;
    color: var(--text-mid);
  }
  .legend span { display: flex; align-items: center; gap: 5px; }
  .legend i { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }

  footer {
    text-align: center;
    padding: 20px;
    font-size: 0.75rem;
    color: var(--text-mid);
  }
</style>
</head>
<body>

<header>
  <h1>PLFantasyBot — 2025-26 Season</h1>
  <div class="subtitle">Best validated simulation · Final score: <b id="final-score"></b> points</div>
</header>

<div class="nav-bar">
  <button id="prev-btn">&larr; Prev</button>
  <select id="gw-select"></select>
  <button id="next-btn">Next &rarr;</button>
  <div class="gw-stats">
    <span id="gw-chip"></span>
    <span>GW score: <b id="gw-score"></b></span>
    <span>Season total: <b id="season-total"></b></span>
    <span id="gw-transfers"></span>
  </div>
</div>

<div class="pitch" id="pitch"></div>
<div class="bench-wrap">
  <div class="bench-label">Bench</div>
  <div class="bench-row" id="bench-row"></div>
</div>

<div class="legend">
  <span><i class="diff-1"></i> Easy (1)</span>
  <span><i class="diff-2"></i> 2</span>
  <span><i class="diff-3"></i> 3</span>
  <span><i class="diff-4"></i> 4</span>
  <span><i class="diff-5"></i> Hard (5)</span>
</div>

<footer>Generated from model/simulate_season.py's best validated run (5-gameweek lookahead, confidence discount). Not real-money advice.</footer>

<script id="squad-data" type="application/json">__SQUAD_DATA__</script>
<script>
  const DATA = JSON.parse(document.getElementById('squad-data').textContent);
  const gameweeks = DATA.gameweeks;
  document.getElementById('final-score').textContent = DATA.final_score;

  const select = document.getElementById('gw-select');
  gameweeks.forEach((g, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = 'Gameweek ' + g.gw + (g.chip ? ' — ' + g.chip : '');
    select.appendChild(opt);
  });

  const POSITION_ORDER = ['GKP', 'DEF', 'MID', 'FWD'];

  function playerCard(p) {
    const diffClass = p.difficulty ? 'diff-' + p.difficulty : 'diff-none';
    let badges = '';
    if (p.is_triple_captain) badges += '<div class="armband tc">3x</div>';
    else if (p.is_effective_captain) badges += '<div class="armband">C</div>';
    else if (p.is_vice_captain) badges += '<div class="armband">V</div>';
    const pointsClass = p.points === 0 ? 'points zero' : 'points';
    return `
      <div class="player-card">
        <div class="badge-row">${badges}</div>
        <div class="kit">${p.position}</div>
        <div class="name">${p.name}</div>
        <div class="opponent ${diffClass}">${p.opponent}</div>
        <div class="${pointsClass}">${p.played ? p.points + ' pts' : 'did not play'}</div>
      </div>`;
  }

  function render(index) {
    const gw = gameweeks[index];
    select.value = index;

    document.getElementById('gw-chip').innerHTML = gw.chip
      ? `<span class="chip-badge">${gw.chip}</span>` : '';
    document.getElementById('gw-score').textContent = gw.gw_score;
    document.getElementById('season-total').textContent = gw.season_total;
    document.getElementById('gw-transfers').textContent =
      gw.transfers != null ? `Transfers: ${gw.transfers}${gw.hits ? ' (-' + gw.hits * 4 + ' pts)' : ''}` : '';

    const pitch = document.getElementById('pitch');
    pitch.innerHTML = '';
    POSITION_ORDER.forEach(pos => {
      const players = gw.starting_xi.filter(p => p.position === pos);
      if (players.length === 0) return;
      const row = document.createElement('div');
      row.className = 'row';
      row.innerHTML = players.map(playerCard).join('');
      pitch.appendChild(row);
    });

    document.getElementById('bench-row').innerHTML = gw.bench.map(playerCard).join('');

    document.getElementById('prev-btn').disabled = index === 0;
    document.getElementById('next-btn').disabled = index === gameweeks.length - 1;
  }

  let current = 0;
  render(current);

  document.getElementById('prev-btn').addEventListener('click', () => {
    if (current > 0) { current--; render(current); }
  });
  document.getElementById('next-btn').addEventListener('click', () => {
    if (current < gameweeks.length - 1) { current++; render(current); }
  });
  select.addEventListener('change', () => {
    current = parseInt(select.value, 10);
    render(current);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft' && current > 0) { current--; render(current); }
    if (e.key === 'ArrowRight' && current < gameweeks.length - 1) { current++; render(current); }
  });
</script>

</body>
</html>
"""


def main() -> None:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    html = TEMPLATE.replace("__SQUAD_DATA__", json.dumps(data))
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Built {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
