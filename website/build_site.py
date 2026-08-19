"""
Builds website/index.html -- a self-contained, single-file site showing every
strategy in model/strategies.py, gameweek by gameweek, in a pitch-view layout.

Reads:
  data/strategies_manifest_2026-27.json  (live season, if generated yet --
      see model/generate_live_strategies.py)
  data/strategies_manifest_2025-26.json  (backtest, from model/run_all_strategies.py)
plus each manifest's referenced per-strategy squads JSON, and embeds all of it
into the page -- no server, no build step, just open the file (or its Vercel
deploy) in a browser.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = Path(__file__).resolve().parent / "index.html"

SEASON_MANIFESTS = [
    ("2026-27", "live", "2026-27 — Live"),
    ("2025-26", "backtest", "2025-26 — Backtest"),
]

RISK_COLORS = {
    "low": "#04a777",
    "medium": "#e8722c",
    "high": "#d0356b",
}

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PLFantasyBot</title>
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

  .season-tabs {
    display: flex;
    justify-content: center;
    gap: 8px;
    background: #2b0030;
    padding: 8px 12px;
    flex-wrap: wrap;
  }
  .season-tabs button {
    background: transparent;
    color: rgba(255,255,255,0.7);
    border: 1px solid rgba(255,255,255,0.25);
    border-radius: 999px;
    padding: 6px 16px;
    font-size: 0.82rem;
    cursor: pointer;
  }
  .season-tabs button.active {
    background: white;
    color: #37003c;
    font-weight: 700;
    border-color: white;
  }

  .strategy-tabs {
    display: flex;
    justify-content: center;
    gap: 10px;
    background: white;
    padding: 14px 12px 6px;
    flex-wrap: wrap;
  }
  .strategy-card {
    width: 190px;
    border: 2px solid var(--card-border);
    border-radius: 10px;
    padding: 10px 12px;
    cursor: pointer;
    text-align: left;
    background: white;
  }
  .strategy-card.active { border-color: var(--risk-color, #37003c); box-shadow: 0 0 0 2px var(--risk-color, #37003c) inset; }
  .strategy-card .label { font-weight: 800; font-size: 0.92rem; }
  .strategy-card .short { font-size: 0.72rem; color: var(--text-mid); margin: 3px 0 6px; }
  .strategy-card .badge-row { display: flex; justify-content: space-between; align-items: center; }
  .strategy-card .risk-badge {
    font-size: 0.62rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.03em; padding: 2px 7px; border-radius: 999px; color: white;
  }
  .strategy-card .score { font-size: 0.85rem; font-weight: 700; }

  .leaderboard {
    max-width: 900px;
    margin: 14px auto 0;
    padding: 0 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
  }
  .leaderboard .row {
    background: white;
    border: 1px solid var(--card-border);
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 0.78rem;
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .leaderboard .rank { font-weight: 800; color: var(--text-mid); }
  .leaderboard .row.current { border-color: #37003c; background: #faf5fb; }

  .description-bar {
    max-width: 900px;
    margin: 12px auto 0;
    padding: 10px 16px;
    font-size: 0.8rem;
    color: var(--text-mid);
    text-align: center;
  }

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
    margin-top: 10px;
  }
  .nav-bar button.nav-btn {
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
  .not-played-badge {
    background: #37003c;
    color: white;
    padding: 2px 10px;
    border-radius: 12px;
    font-weight: 700;
    font-size: 0.78rem;
  }

  .deadline-banner {
    text-align: center;
    padding: 10px 16px;
    font-size: 0.85rem;
    font-weight: 700;
    color: white;
    background: #1f8a45;
  }
  .deadline-banner.urgent { background: #d0356b; }
  .deadline-banner.passed { background: #5a6472; }

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
    z-index: 2;
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
  .player-card .ownership-badge {
    position: absolute;
    top: 3px;
    left: 3px;
    background: rgba(26,26,46,0.75);
    color: white;
    font-size: 0.55rem;
    font-weight: 700;
    padding: 1px 5px;
    border-radius: 999px;
    z-index: 2;
  }
  .player-card.flagged { box-shadow: 0 0 0 2px var(--flag-color, #d0356b); }
  .player-card .status-badge {
    background: var(--flag-color, #d0356b);
    color: white;
    font-size: 0.58rem;
    font-weight: 800;
    text-align: center;
    padding: 2px;
    letter-spacing: 0.03em;
    cursor: help;
  }
  .player-card .photo-wrap {
    height: 64px;
    background: #37003c;
    display: flex;
    align-items: flex-end;
    justify-content: center;
    overflow: hidden;
  }
  .player-card .photo-wrap img {
    height: 76px;
    object-fit: cover;
    object-position: top;
  }
  .player-card .photo-wrap .initials {
    color: white;
    font-size: 1.1rem;
    font-weight: 800;
    opacity: 0.6;
    padding-bottom: 14px;
  }
  .player-card .kit {
    height: 16px;
    background: #2b0030;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-size: 0.6rem;
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
  <h1>PLFantasyBot</h1>
  <div class="subtitle" id="header-subtitle"></div>
</header>

<div class="deadline-banner" id="deadline-banner" style="display:none"></div>

<div class="season-tabs" id="season-tabs"></div>
<div class="strategy-tabs" id="strategy-tabs"></div>
<div class="leaderboard" id="leaderboard"></div>
<div class="description-bar" id="description-bar"></div>

<div class="nav-bar">
  <button class="nav-btn" id="prev-btn">&larr; Prev</button>
  <select id="gw-select"></select>
  <button class="nav-btn" id="next-btn">Next &rarr;</button>
  <div class="gw-stats">
    <span id="gw-chip"></span>
    <span id="gw-score-wrap">GW score: <b id="gw-score"></b></span>
    <span id="season-total-wrap">Total so far: <b id="season-total"></b></span>
    <span id="gw-transfers"></span>
  </div>
</div>

<div class="pitch" id="pitch"></div>
<div class="bench-wrap">
  <div class="bench-label">Bench</div>
  <div class="bench-row" id="bench-row"></div>
</div>

<div class="legend">
  <span><i class="diff-1"></i> Easy fixture (1)</span>
  <span><i class="diff-2"></i> 2</span>
  <span><i class="diff-3"></i> 3</span>
  <span><i class="diff-4"></i> 4</span>
  <span><i class="diff-5"></i> Hard (5)</span>
</div>

<footer>Generated from model/run_all_strategies.py and model/generate_live_strategies.py. Not real-money advice.</footer>

<script id="site-data" type="application/json">__SITE_DATA__</script>
<script>
  const DATA = JSON.parse(document.getElementById('site-data').textContent);
  const RISK_COLORS = {"low": "#04a777", "medium": "#e8722c", "high": "#d0356b"};
  const POSITION_ORDER = ['GKP', 'DEF', 'MID', 'FWD'];

  let seasonIdx = 0;
  let strategyIdx = 0;
  let gwIdx = 0;
  let currentDeadlineISO = null;

  function tickDeadline() {
    const banner = document.getElementById('deadline-banner');
    if (!currentDeadlineISO) { banner.style.display = 'none'; return; }

    const deadline = new Date(currentDeadlineISO);
    const now = new Date();
    const diffMs = deadline - now;
    const local = deadline.toLocaleString(undefined, {
      weekday: 'short', day: 'numeric', month: 'short',
      hour: '2-digit', minute: '2-digit',
    });

    banner.style.display = '';
    banner.classList.remove('urgent', 'passed');

    if (diffMs <= 0) {
      banner.classList.add('passed');
      banner.textContent = `Deadline passed — ${local} (this squad is now locked)`;
      return;
    }

    const totalSeconds = Math.floor(diffMs / 1000);
    const days = Math.floor(totalSeconds / 86400);
    const hours = Math.floor((totalSeconds % 86400) / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const parts = [];
    if (days) parts.push(`${days}d`);
    if (days || hours) parts.push(`${hours}h`);
    if (days || hours || minutes) parts.push(`${minutes}m`);
    parts.push(`${seconds}s`);

    if (diffMs < 24 * 3600 * 1000) banner.classList.add('urgent');
    banner.textContent = `Deadline: ${local} — ${parts.join(' ')} remaining`;
  }

  setInterval(tickDeadline, 1000);

  function currentSeason() { return DATA.seasons[seasonIdx]; }
  function currentStrategy() { return currentSeason().strategies[strategyIdx]; }

  function photoTag(p) {
    if (p.photo_code) {
      const url = `https://resources.premierleague.com/premierleague/photos/players/110x140/p${p.photo_code}.png`;
      return `<img src="${url}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'initials',textContent:'${(p.name||'?').split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase()}'}))">`;
    }
    const initials = (p.name || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
    return `<div class="initials">${initials}</div>`;
  }

  const STATUS_LABELS = {
    i: ['INJURED', '#d0356b'], s: ['SUSPENDED', '#d0356b'],
    d: ['DOUBTFUL', '#e8722c'], u: ['UNAVAILABLE', '#5a6472'],
    n: ['NOT IN SQUAD', '#5a6472'],
  };

  function playerCard(p) {
    const diffClass = p.difficulty ? 'diff-' + p.difficulty : 'diff-none';
    let badges = '';
    if (p.is_triple_captain) badges += '<div class="armband tc">3x</div>';
    else if (p.is_effective_captain) badges += '<div class="armband">C</div>';
    else if (p.is_vice_captain) badges += '<div class="armband">V</div>';
    const pointsClass = p.points === 0 ? 'points zero' : 'points';
    const pointsLabel = p.gw_not_played ? 'not yet played' : (p.played ? p.points + ' pts' : 'did not play');
    const ownershipBadge = (p.ownership != null) ? `<div class="ownership-badge">${p.ownership.toFixed(1)}%</div>` : '';
    const [label, color] = STATUS_LABELS[p.status] || [null, null];
    let statusBadge = '';
    if (label) {
      const chance = (p.chance_of_playing != null) ? ` (${p.chance_of_playing}% chance)` : '';
      const tip = (p.news ? p.news : label) + chance;
      statusBadge = `<div class="status-badge" style="--flag-color:${color}" title="${tip.replace(/"/g, '&quot;')}">${label}${chance}</div>`;
    }
    return `
      <div class="player-card ${label ? 'flagged' : ''}" style="${color ? `--flag-color:${color}` : ''}">
        <div class="badge-row">${badges}</div>
        ${ownershipBadge}
        <div class="photo-wrap">${photoTag(p)}</div>
        <div class="kit">${p.position}</div>
        <div class="name">${p.name}</div>
        <div class="opponent ${diffClass}">${p.opponent}</div>
        <div class="${pointsClass}">${pointsLabel}</div>
        ${statusBadge}
      </div>`;
  }

  function renderSeasonTabs() {
    const el = document.getElementById('season-tabs');
    if (DATA.seasons.length <= 1) { el.style.display = 'none'; return; }
    el.innerHTML = DATA.seasons.map((s, i) =>
      `<button class="${i === seasonIdx ? 'active' : ''}" data-i="${i}">${s.label}</button>`
    ).join('');
    el.querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => {
      seasonIdx = parseInt(btn.dataset.i, 10);
      strategyIdx = 0; gwIdx = 0;
      renderAll();
    }));
  }

  function scoreLabel(strategy) {
    const gws = strategy.gameweeks;
    const last = gws.length ? gws[gws.length - 1] : null;
    if (!last || last.season_total == null) return 'Not yet played';
    return last.season_total + ' pts';
  }

  function renderStrategyTabs() {
    const el = document.getElementById('strategy-tabs');
    const season = currentSeason();
    el.innerHTML = season.strategies.map((s, i) => {
      const color = RISK_COLORS[s.risk_tier] || '#37003c';
      return `
        <div class="strategy-card ${i === strategyIdx ? 'active' : ''}" style="--risk-color:${color}" data-i="${i}">
          <div class="badge-row">
            <span class="label">${s.label}</span>
          </div>
          <div class="short">${s.short}</div>
          <div class="badge-row">
            <span class="risk-badge" style="background:${color}">${s.risk}</span>
            <span class="score">${scoreLabel(s)}</span>
          </div>
        </div>`;
    }).join('');
    el.querySelectorAll('.strategy-card').forEach(card => card.addEventListener('click', () => {
      strategyIdx = parseInt(card.dataset.i, 10);
      gwIdx = 0;
      renderAll();
    }));
  }

  function renderLeaderboard() {
    const el = document.getElementById('leaderboard');
    const season = currentSeason();
    const ranked = season.strategies.map((s, i) => {
      const gws = s.gameweeks;
      const last = gws.length ? gws[gws.length - 1] : null;
      const total = (last && last.season_total != null) ? last.season_total : null;
      return { i, label: s.label, total };
    }).sort((a, b) => (b.total ?? -Infinity) - (a.total ?? -Infinity));
    el.innerHTML = ranked.map((r, rank) => `
      <div class="row ${r.i === strategyIdx ? 'current' : ''}">
        <span class="rank">#${rank + 1}</span>
        <span>${r.label}</span>
        <b>${r.total == null ? '—' : r.total + ' pts'}</b>
      </div>`).join('');
  }

  function renderDescription() {
    document.getElementById('description-bar').textContent = currentStrategy().description;
  }

  function renderHeader() {
    const season = currentSeason();
    document.getElementById('header-subtitle').textContent =
      `${season.label} · ${currentStrategy().label} strategy`;
  }

  function render() {
    const strategy = currentStrategy();
    const gameweeks = strategy.gameweeks;
    const select = document.getElementById('gw-select');
    select.innerHTML = '';
    gameweeks.forEach((g, i) => {
      const opt = document.createElement('option');
      opt.value = i;
      opt.textContent = 'Gameweek ' + g.gw + (g.chip ? ' — ' + g.chip : '') + (g.season_total == null ? ' (not yet played)' : '');
      select.appendChild(opt);
    });

    if (gwIdx >= gameweeks.length) gwIdx = 0;
    const gw = gameweeks[gwIdx];
    select.value = gwIdx;

    const notPlayed = gw.season_total == null;
    document.getElementById('gw-chip').innerHTML = gw.chip
      ? `<span class="chip-badge">${gw.chip}</span>`
      : (notPlayed ? `<span class="not-played-badge">Not yet played</span>` : '');
    document.getElementById('gw-score-wrap').style.display = notPlayed ? 'none' : '';
    document.getElementById('season-total-wrap').style.display = notPlayed ? 'none' : '';
    document.getElementById('gw-score').textContent = gw.gw_score;
    document.getElementById('season-total').textContent = gw.season_total;
    document.getElementById('gw-transfers').textContent =
      gw.transfers != null ? `Transfers: ${gw.transfers}${gw.hits ? ' (-' + gw.hits * 4 + ' pts)' : ''}` : '';

    currentDeadlineISO = gw.deadline || null;
    tickDeadline();

    const withFlag = (p) => Object.assign({}, p, { gw_not_played: notPlayed });

    const pitch = document.getElementById('pitch');
    pitch.innerHTML = '';
    POSITION_ORDER.forEach(pos => {
      const players = gw.starting_xi.filter(p => p.position === pos);
      if (players.length === 0) return;
      const row = document.createElement('div');
      row.className = 'row';
      row.innerHTML = players.map(withFlag).map(playerCard).join('');
      pitch.appendChild(row);
    });

    document.getElementById('bench-row').innerHTML = gw.bench.map(withFlag).map(playerCard).join('');

    document.getElementById('prev-btn').disabled = gwIdx === 0;
    document.getElementById('next-btn').disabled = gwIdx === gameweeks.length - 1;
  }

  function renderAll() {
    renderSeasonTabs();
    renderStrategyTabs();
    renderLeaderboard();
    renderDescription();
    renderHeader();
    render();
  }

  document.getElementById('prev-btn').addEventListener('click', () => {
    if (gwIdx > 0) { gwIdx--; render(); }
  });
  document.getElementById('next-btn').addEventListener('click', () => {
    const max = currentStrategy().gameweeks.length - 1;
    if (gwIdx < max) { gwIdx++; render(); }
  });
  document.getElementById('gw-select').addEventListener('change', (e) => {
    gwIdx = parseInt(e.target.value, 10);
    render();
  });
  document.addEventListener('keydown', (e) => {
    const max = currentStrategy().gameweeks.length - 1;
    if (e.key === 'ArrowLeft' && gwIdx > 0) { gwIdx--; render(); }
    if (e.key === 'ArrowRight' && gwIdx < max) { gwIdx++; render(); }
  });

  renderAll();
</script>

</body>
</html>
"""


def load_season_block(season: str, kind: str, label: str) -> dict | None:
    manifest_path = DATA_DIR / f"strategies_manifest_{season}.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    strategies = []
    for entry in sorted(manifest["strategies"], key=lambda e: e["order"]):
        squads_path = DATA_DIR / entry["squads_file"]
        squads = json.loads(squads_path.read_text(encoding="utf-8"))
        strategies.append({**entry, "gameweeks": squads["gameweeks"]})

    return {"key": season, "kind": kind, "label": label, "strategies": strategies}


def main() -> None:
    seasons = []
    for season, kind, label in SEASON_MANIFESTS:
        block = load_season_block(season, kind, label)
        if block is not None:
            seasons.append(block)

    if not seasons:
        raise SystemExit(
            "No strategies_manifest_*.json found in data/ -- run "
            "model/run_all_strategies.py (and/or model/generate_live_strategies.py) first."
        )

    site_data = {"seasons": seasons}
    html = TEMPLATE.replace("__SITE_DATA__", json.dumps(site_data))
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Built {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")
    for s in seasons:
        print(f"  {s['label']}: {len(s['strategies'])} strategies")


if __name__ == "__main__":
    main()
