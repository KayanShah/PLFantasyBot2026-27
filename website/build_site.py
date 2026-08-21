"""
Builds website/index.html -- a self-contained, single-file site showing every
strategy in model/strategies.py, gameweek by gameweek.

Visual design is the "light glass" desktop UI handed off in
~/Downloads/plfantasybot-ui-light/index.html (see that folder's README) --
this script keeps its HTML/CSS untouched and replaces only its demo data and
rendering script with real data, wired to the same nested season/strategy/
gameweek JSON shape the previous build_site.py already produced.

Reads:
  data/strategies_manifest_2026-27.json  (live season, if generated yet --
      see model/generate_live_strategies.py)
  data/strategies_manifest_2025-26.json  (backtest, from model/run_all_strategies.py)
plus each manifest's referenced per-strategy squads JSON and
data/live_gameweek_calendar.json, and embeds all of it into the page -- no
server, no build step, just open the file (or its Vercel deploy).
"""

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = Path(__file__).resolve().parent / "index.html"

SEASON_MANIFESTS = [
    ("2026-27", "live", "2026-27 — Live"),
    ("2025-26", "backtest", "2025-26 — Backtest"),
]


def build_team_shortcodes() -> dict[str, str]:
    """
    Full team name -> short code (e.g. "Man City" -> "MCI"), merged across
    every season's teams.csv. Player entries carry the full name (that's
    what merged_gw.csv uses), but this UI's design displays a compact code
    next to the opponent (also already a short code) -- club identity is
    stable enough season to season that one combined map covers every
    season's squads without needing to know which season a row came from.
    """
    mapping: dict[str, str] = {}
    for teams_csv in DATA_DIR.glob("historical/*/teams.csv"):
        with teams_csv.open(encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                if row.get("name") and row.get("short_name"):
                    mapping[row["name"]] = row["short_name"]
    return mapping


def apply_shortcodes(gameweeks: list[dict], shortcodes: dict[str, str]) -> list[dict]:
    for gw in gameweeks:
        for player in gw.get("starting_xi", []) + gw.get("bench", []):
            player["team"] = shortcodes.get(player.get("team"), player.get("team"))
    return gameweeks


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PLFantasyBot</title>
  <style>
    :root {
      --bg: #f5f5f7;
      --bg-2: #fbfbfd;
      --surface: rgba(255,255,255,.66);
      --surface-strong: rgba(255,255,255,.86);
      --surface-soft: rgba(255,255,255,.54);
      --line: rgba(15,23,42,.075);
      --line-strong: rgba(15,23,42,.12);
      --text: #1d1d1f;
      --muted: #6e6e73;
      --muted-2: #9a9aa0;
      --purple: #5f259f;
      --purple-soft: #ede5f7;
      --green: #1f9d68;
      --green-soft: #e8f6ef;
      --orange: #c87335;
      --pink: #c9456e;
      --gold: #b7872f;
      --danger: #c83f57;
      --pitch-1: #b8e6c9;
      --pitch-2: #a8dcbc;
      --pitch-line: rgba(255,255,255,.68);
      --shadow-xl: 0 26px 70px rgba(30,33,43,.10), 0 2px 7px rgba(30,33,43,.05);
      --shadow-md: 0 14px 34px rgba(30,33,43,.075), 0 1px 2px rgba(30,33,43,.04);
      --shadow-sm: 0 7px 20px rgba(30,33,43,.06), 0 1px 2px rgba(30,33,43,.04);
      --inner: inset 0 1px 0 rgba(255,255,255,.86);
      --radius-xl: 30px;
      --radius-lg: 24px;
      --radius-md: 18px;
      --radius-sm: 13px;
      --ease: cubic-bezier(.2,.8,.2,1);
    }

    * { box-sizing: border-box; }
    html { color-scheme: light; }
    body {
      margin: 0;
      min-width: 1180px;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", Inter, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 10% -10%, rgba(119,82,157,.10), transparent 28%),
        radial-gradient(circle at 88% 4%, rgba(112,191,157,.10), transparent 24%),
        linear-gradient(180deg, #fbfbfd 0%, #f4f4f6 48%, #f7f7f9 100%);
      overflow-x: auto;
      -webkit-font-smoothing: antialiased;
      text-rendering: geometricPrecision;
    }

    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        radial-gradient(circle at 50% 0%, rgba(255,255,255,.90), transparent 46%),
        linear-gradient(120deg, rgba(255,255,255,.28), transparent 30% 70%, rgba(255,255,255,.18));
      opacity: .72;
      z-index: -1;
    }

    button, select { font: inherit; }
    button { color: inherit; }

    .app-shell {
      width: min(1640px, calc(100vw - 72px));
      margin: 32px auto 56px;
    }

    .glass {
      background: linear-gradient(135deg, rgba(255,255,255,.76), rgba(255,255,255,.50));
      border: 1px solid rgba(255,255,255,.86);
      box-shadow: var(--shadow-md), var(--inner);
      backdrop-filter: blur(30px) saturate(155%);
      -webkit-backdrop-filter: blur(30px) saturate(155%);
    }

    .topbar {
      min-height: 80px;
      padding: 13px 15px 13px 19px;
      border-radius: 25px;
      display: grid;
      grid-template-columns: 1fr auto auto auto;
      gap: 18px;
      align-items: center;
      position: sticky;
      top: 18px;
      z-index: 50;
      background: rgba(255,255,255,.73);
      border: 1px solid rgba(255,255,255,.94);
      box-shadow: 0 18px 48px rgba(33,35,44,.09), inset 0 1px 0 rgba(255,255,255,.96);
      backdrop-filter: blur(34px) saturate(170%);
      -webkit-backdrop-filter: blur(34px) saturate(170%);
    }

    .brand { display:flex; align-items:center; gap:13px; min-width: 290px; }
    .brand-mark {
      width: 45px; height: 45px; border-radius: 13px;
      display:grid; place-items:center;
      background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(247,247,249,.90));
      border: 1px solid rgba(29,29,31,.08);
      box-shadow: 0 7px 18px rgba(38,39,46,.08), inset 0 1px 0 white;
      font-size: 0;
      font-weight: 760;
      letter-spacing: -.04em;
      color: var(--text);
      position: relative;
    }
    .brand-mark::before { content:"PL"; font-size:14px; }
    .brand-mark::after {
      content:""; position:absolute; width:7px; height:7px; border-radius:50%;
      right:7px; bottom:7px; background:var(--purple); box-shadow:0 0 0 3px white;
    }
    .brand h1 { margin:0; font-size: 19px; line-height:1.1; letter-spacing: -.035em; font-weight: 690; }
    .brand p { margin:4px 0 0; color:var(--muted); font-size: 12px; letter-spacing:-.01em; }

    .segmented {
      display:inline-flex; gap:3px; padding:4px;
      border-radius: 14px;
      background: rgba(240,240,243,.83);
      border: 1px solid rgba(29,29,31,.055);
      box-shadow: inset 0 1px 2px rgba(29,29,31,.055), 0 1px 0 rgba(255,255,255,.95);
    }
    .seg-btn {
      border:0; background: transparent; color: #737379;
      padding: 9px 14px; border-radius: 11px; cursor:pointer;
      font-size: 12px; font-weight: 620;
      transition: .2s var(--ease);
    }
    .seg-btn:hover { color:var(--text); }
    .seg-btn.active {
      color:var(--text);
      background: rgba(255,255,255,.94);
      box-shadow: 0 4px 12px rgba(29,29,31,.08), inset 0 0 0 1px rgba(29,29,31,.035), inset 0 1px 0 white;
    }

    .countdown-chip {
      display:flex; align-items:center; gap:11px;
      min-width: 365px;
      padding: 8px 10px 8px 13px;
      border-radius: 16px;
      background: rgba(250,250,252,.78);
      border: 1px solid rgba(29,29,31,.06);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.94), 0 5px 16px rgba(29,29,31,.045);
    }
    .countdown-chip .dot {
      width: 8px; height:8px; border-radius:50%; background: #24a46d;
      box-shadow: 0 0 0 5px rgba(36,164,109,.08);
    }
    .countdown-chip.urgent .dot { background:#d5952c; box-shadow:0 0 0 5px rgba(213,149,44,.09); }
    .countdown-chip.passed .dot { background:#ce4a5d; box-shadow:0 0 0 5px rgba(206,74,93,.09); }
    .countdown-meta { min-width: 130px; }
    .countdown-meta strong { display:block; font-size: 11.5px; font-weight:650; }
    .countdown-meta span { display:block; margin-top:3px; color:var(--muted); font-size:9.5px; }
    .time-cells { margin-left:auto; display:flex; gap:4px; }
    .time-cell {
      width: 42px; min-height: 40px; text-align:center; border-radius:10px; padding:5px 2px 4px;
      background:rgba(255,255,255,.75); border:1px solid rgba(29,29,31,.045);
      box-shadow: inset 0 1px 0 white;
    }
    .time-cell b { display:block; font-size: 13px; font-variant-numeric:tabular-nums; }
    .time-cell small { display:block; margin-top:1px; color:var(--muted-2); font-size:7.5px; text-transform:uppercase; letter-spacing:.10em; }

    .strategy-strip {
      margin-top: 20px;
      display:grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 4px;
      padding: 6px;
      border-radius: 24px;
      background: rgba(255,255,255,.48);
      border: 1px solid rgba(255,255,255,.82);
      box-shadow: 0 12px 30px rgba(35,37,45,.06), inset 0 1px 0 rgba(255,255,255,.95);
      backdrop-filter: blur(28px) saturate(150%);
      -webkit-backdrop-filter: blur(28px) saturate(150%);
    }
    .strategy-card {
      position:relative; overflow:hidden;
      min-height: 92px;
      padding: 14px 15px 13px;
      border-radius: 18px;
      cursor:pointer;
      transition: transform .22s var(--ease), box-shadow .22s, border-color .22s, background .22s;
      background: transparent;
      border:1px solid transparent;
      box-shadow:none;
    }
    .strategy-card:hover { background:rgba(255,255,255,.43); }
    .strategy-card.active {
      background:rgba(255,255,255,.88);
      border-color:rgba(29,29,31,.045);
      box-shadow:0 8px 22px rgba(42,35,52,.075), inset 0 1px 0 white;
    }
    .strategy-card.active::before {
      content:""; position:absolute; left:50%; bottom:7px; width:5px; height:5px; transform:translateX(-50%); border-radius:50%;
      background:var(--purple);
    }
    .strategy-card::after { display:none; }
    .strategy-top { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
    .strategy-card h3 { margin:0; font-size: 13px; letter-spacing:-.02em; font-weight:670; }
    .strategy-card p { margin:8px 0 0; color:var(--muted); font-size: 10.8px; line-height:1.4; }
    .risk-pill { flex:0 0 auto; font-size:8.7px; font-weight:690; padding:5px 7px; border-radius:999px; border:1px solid transparent; }
    .risk-low { color:#34755e; background:#edf7f2; border-color:#d7eee4; }
    .risk-medium { color:#92623f; background:#fbf2ea; border-color:#f2e1d2; }
    .risk-high { color:#9b4d68; background:#f9edf1; border-color:#f0d8e0; }

    .main-grid {
      margin-top: 18px;
      display:grid;
      grid-template-columns: minmax(800px, 1fr) 360px;
      gap: 18px;
      align-items:start;
    }

    .pitch-panel {
      border-radius: var(--radius-xl);
      overflow:hidden;
      position:relative;
      box-shadow: var(--shadow-xl), inset 0 1px 0 rgba(255,255,255,.95);
      border:1px solid rgba(255,255,255,.92);
      background: rgba(255,255,255,.72);
      backdrop-filter: blur(30px) saturate(140%);
      -webkit-backdrop-filter: blur(30px) saturate(140%);
    }
    .panel-head {
      padding: 20px 22px 17px;
      display:flex; align-items:center; justify-content:space-between; gap:18px;
      border-bottom: 1px solid rgba(29,29,31,.055);
      background:rgba(255,255,255,.30);
    }
    .head-copy h2 { margin:0; font-size:17px; letter-spacing:-.025em; font-weight:690; }
    .head-copy p { margin:5px 0 0; color:var(--muted); font-size:11px; }
    .head-actions { display:flex; gap:7px; align-items:center; }
    .ghost-btn, .select-shell {
      height: 38px;
      border-radius: 12px;
      border:1px solid rgba(29,29,31,.06);
      background: rgba(255,255,255,.74);
      color:var(--text);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.95), 0 4px 12px rgba(29,29,31,.045);
    }
    .ghost-btn { width:38px; padding:0; cursor:pointer; }
    .ghost-btn:hover { background:white; box-shadow:0 6px 16px rgba(29,29,31,.07), inset 0 1px 0 white; }
    .ghost-btn:disabled { opacity:.35; cursor:default; }
    .select-shell { position:relative; min-width: 128px; }
    .select-shell select { width:100%; height:100%; appearance:none; border:0; outline:0; background:transparent; color:var(--text); padding:0 34px 0 12px; cursor:pointer; font-size:11.5px; font-weight:580; }
    .select-shell::after { content:"⌄"; position:absolute; right:12px; top:8px; pointer-events:none; color:var(--muted); }

    .gw-summary {
      display:grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 9px;
      padding: 14px 18px;
      border-bottom: 1px solid rgba(29,29,31,.05);
      background: rgba(249,249,251,.68);
    }
    .metric {
      min-height: 63px;
      padding: 12px 13px;
      border-radius: 15px;
      background: rgba(255,255,255,.80);
      border:1px solid rgba(29,29,31,.05);
      box-shadow: inset 0 1px 0 white, 0 3px 10px rgba(29,29,31,.035);
    }
    .metric label { display:block; color:var(--muted); font-size:8.5px; text-transform:uppercase; letter-spacing:.09em; }
    .metric strong { display:block; margin-top:7px; font-size:14.5px; font-weight:680; }
    .metric strong.accent { color:var(--purple); }

    .pitch {
      position:relative;
      margin: 18px;
      min-height: 720px;
      border-radius: 25px;
      overflow:hidden;
      background:
        linear-gradient(180deg, transparent 49.82%, var(--pitch-line) 49.92%, var(--pitch-line) 50.08%, transparent 50.18%),
        repeating-linear-gradient(180deg, rgba(255,255,255,.055) 0 74px, rgba(255,255,255,.018) 74px 148px),
        linear-gradient(180deg, #a8dbbc 0%, #9ed4b4 18%, #95cda9 38%, #91c8a4 58%, #89c39f 100%);
      border: 1px solid rgba(51,118,81,.10);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.6), inset 0 0 46px rgba(31,109,69,.08), 0 8px 24px rgba(38,92,62,.07);
    }
    .pitch::before {
      content:"";
      position:absolute; inset: 28px;
      border: 2px solid var(--pitch-line);
      border-radius: 6px;
      pointer-events:none;
    }
    .pitch::after {
      content:"";
      position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
      width: 142px; height:142px; border-radius:50%; border:2px solid var(--pitch-line);
      pointer-events:none;
      box-shadow: 0 0 0 0 var(--pitch-line);
    }
    .halfway-line {
      position:absolute; left:28px; right:28px; top:50%; height:2px; transform:translateY(-50%);
      background: var(--pitch-line); z-index:0;
    }
    .center-spot,
    .penalty-spot {
      position:absolute; left:50%; transform:translate(-50%,-50%);
      width:8px; height:8px; border-radius:50%; background:var(--pitch-line); z-index:0;
    }
    .center-spot { top:50%; }
    .penalty-spot.top { top:124px; }
    .penalty-spot.bottom { top:calc(100% - 124px); }
    .penalty { position:absolute; left:50%; transform:translateX(-50%); width:315px; height:108px; border:2px solid var(--pitch-line); z-index:0; }
    .penalty.top { top:28px; border-top:0; }
    .penalty.bottom { bottom:28px; border-bottom:0; }
    .six-yard { position:absolute; left:50%; transform:translateX(-50%); width:155px; height:48px; border:2px solid var(--pitch-line); z-index:0; }
    .six-yard.top { top:28px; border-top:0; }
    .six-yard.bottom { bottom:28px; border-bottom:0; }
    .arc {
      position:absolute; left:50%; transform:translateX(-50%); width:126px; height:62px; z-index:0;
      border:2px solid var(--pitch-line); border-left-color:transparent; border-right-color:transparent;
      background: transparent;
    }
    .arc.top { top:104px; border-top-color:transparent; border-radius:0 0 70px 70px; }
    .arc.bottom { bottom:104px; border-bottom-color:transparent; border-radius:70px 70px 0 0; }
    .goal {
      position:absolute; left:50%; transform:translateX(-50%); width:122px; height:16px; z-index:0;
      border-left:2px solid rgba(255,255,255,.52); border-right:2px solid rgba(255,255,255,.52);
    }
    .goal.top { top:12px; border-top:2px solid rgba(255,255,255,.52); border-radius:0 0 8px 8px; }
    .goal.bottom { bottom:12px; border-bottom:2px solid rgba(255,255,255,.52); border-radius:8px 8px 0 0; }

    .squad {
      position:relative; z-index:3;
      min-height: 720px;
      padding: 38px 34px 32px;
      display:grid;
      grid-template-rows: .95fr 1fr 1fr .85fr;
      align-items:center;
      gap: 4px;
    }
    .position-row { display:flex; align-items:center; justify-content:center; gap: clamp(30px, 4vw, 72px); flex-wrap: wrap; }

    .player-card {
      position:relative;
      width: 126px;
      transition: transform .22s var(--ease), filter .22s;
      filter: drop-shadow(0 8px 14px rgba(27,64,42,.10));
    }
    .player-card:hover { transform: translateY(-4px); filter: drop-shadow(0 14px 20px rgba(27,64,42,.14)); z-index:10; }

    .player-visual {
      height: 91px;
      border-radius: 19px 19px 12px 12px;
      display:flex; align-items:flex-end; justify-content:center;
      position:relative; overflow:hidden;
      background: linear-gradient(180deg, rgba(255,255,255,.72), rgba(255,255,255,.36));
      border: 1px solid rgba(255,255,255,.77);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.92), 0 8px 20px rgba(40,83,58,.09);
      backdrop-filter: blur(16px) saturate(125%);
      -webkit-backdrop-filter: blur(16px) saturate(125%);
    }
    .player-visual::before {
      content:""; position:absolute; left:10px; top:10px; width:28px; height:3px; border-radius:999px;
      background:linear-gradient(90deg,var(--club-a),var(--club-b)); opacity:.8;
    }
    .avatar {
      width: 62px; height: 62px; border-radius: 50%;
      display:grid; place-items:center;
      font-size:19px; font-weight:720; letter-spacing:-.04em;
      color:#38383c;
      background: rgba(255,255,255,.76);
      border: 1px solid rgba(29,29,31,.06);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.95), 0 7px 14px rgba(29,29,31,.06);
      margin-bottom: 8px;
      position:relative;
    }
    .player-photo { width:82px; height:91px; object-fit:contain; object-position:bottom center; position:relative; z-index:1; }
    .badges { position:absolute; top:7px; right:7px; left:auto; display:flex; gap:4px; z-index:4; }
    .badge {
      min-width:23px; height:23px; padding:0 6px; border-radius:8px;
      display:grid; place-items:center; font-size:8.5px; font-weight:760;
      background:rgba(255,255,255,.88); color:var(--text); border:1px solid rgba(29,29,31,.07);
      box-shadow:0 5px 12px rgba(29,29,31,.08), inset 0 1px 0 white;
      backdrop-filter:blur(10px);
    }
    .badge.captain { background:#222226; color:white; border-color:#222226; }
    .badge.vice { background:rgba(255,255,255,.96); color:#333338; }
    .badge.status { background:#d95669; color:white; border-color:#d95669; }
    .badge.status.fit { background:rgba(255,255,255,.88); color:var(--muted); border-color:rgba(29,29,31,.07); }

    .player-info {
      margin-top: 5px;
      border-radius: 12px;
      overflow:hidden;
      background:rgba(255,255,255,.84);
      border:1px solid rgba(255,255,255,.92);
      box-shadow: 0 6px 16px rgba(33,62,45,.08), inset 0 1px 0 white;
      backdrop-filter: blur(16px) saturate(130%);
      -webkit-backdrop-filter: blur(16px) saturate(130%);
    }
    .player-name { padding:7px 8px 3px; text-align:center; font-size:10.2px; font-weight:670; color:#2a2a2d; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .player-sub { padding:0 7px 7px; display:flex; justify-content:center; align-items:center; gap:5px; color:#68686d; font-size:8.2px; }
    .fdr { width:18px; height:18px; display:grid; place-items:center; border-radius:6px; font-weight:760; color:#314137; }
    .fdr-1 { background:#d8f3e5; } .fdr-2 { background:#e7f1d0; } .fdr-3 { background:#f6edca; } .fdr-4 { background:#f4d8c5; } .fdr-5 { background:#f2cdd6; }
    .points { margin-left:auto; font-weight:720; color:#242426; }
    .ownership { color:#85858a; }

    .bench-wrap {
      margin: 0 18px 18px;
      padding: 15px 18px 16px;
      border-radius: 22px;
      background: rgba(249,249,251,.72);
      border:1px solid rgba(29,29,31,.05);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.95), 0 8px 24px rgba(29,29,31,.045);
    }
    .bench-title { display:flex; justify-content:space-between; align-items:center; margin-bottom:13px; }
    .bench-title strong { font-size:10px; text-transform:uppercase; letter-spacing:.11em; color:#6f6f74; }
    .bench-title span { color:var(--muted); font-size:9.5px; }
    .bench-row { display:flex; justify-content:space-around; gap:18px; flex-wrap: wrap; }
    .bench-row .player-card { transform:scale(.90); transform-origin:center; }
    .bench-row .player-card:hover { transform:scale(.92) translateY(-3px); }

    .sidebar { display:flex; flex-direction:column; gap:18px; }
    .side-card { border-radius: 22px; padding: 18px; }
    .side-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:14px; }
    .side-head h3 { margin:0; font-size:13px; letter-spacing:-.015em; font-weight:680; }
    .side-head span { color:var(--muted); font-size:9.5px; }

    .leader-row {
      display:grid; grid-template-columns:30px 1fr auto;
      align-items:center; gap:10px;
      padding: 10px 9px;
      border-radius: 12px;
      border:1px solid transparent;
      transition:.18s;
      cursor: pointer;
    }
    .leader-row + .leader-row { margin-top:4px; }
    .leader-row.active { background:rgba(95,37,159,.06); border-color:rgba(95,37,159,.10); }
    .rank { color:var(--muted-2); font-size:10px; font-weight:700; }
    .leader-name { font-size:10.8px; font-weight:630; }
    .leader-name small { display:block; color:var(--muted-2); margin-top:2px; font-weight:480; }
    .leader-score { font-size:11.5px; font-weight:720; }

    .strategy-detail {
      padding: 15px;
      border-radius: 16px;
      background: rgba(248,246,251,.76);
      border:1px solid rgba(95,37,159,.075);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.96);
    }
    .strategy-detail h4 { margin:0; font-size:12.5px; font-weight:680; }
    .strategy-detail p { margin:8px 0 0; color:var(--muted); font-size:10.2px; line-height:1.55; }

    .info-panel { margin-top: 18px; display:flex; flex-direction:column; gap:16px; }
    .info-card { border-radius: var(--radius-xl); padding: 24px 26px; }
    .info-card h2 { margin:0 0 8px; font-size:16px; font-weight:690; letter-spacing:-.02em; }
    .info-card > p { margin:8px 0; color:var(--muted); font-size:11.5px; line-height:1.65; max-width:820px; }
    .info-grid { display:grid; grid-template-columns: repeat(2, 1fr); gap:12px; margin-top:12px; }
    .info-item {
      padding:14px 15px;
      border-radius:16px;
      background:rgba(249,249,251,.7);
      border:1px solid rgba(29,29,31,.05);
      font-size:10.8px; color:var(--muted); line-height:1.6;
    }
    .info-item strong { display:block; color:var(--text); font-size:11.5px; font-weight:670; margin-bottom:4px; }
    .info-strategy-card { padding:14px 15px; border-radius:16px; background:rgba(249,249,251,.7); border:1px solid rgba(29,29,31,.05); }
    .info-strategy-card .strategy-top { margin-bottom:6px; }
    .info-strategy-card h3 { margin:0; font-size:12px; font-weight:670; }
    .info-strategy-card p { margin:0; color:var(--muted); font-size:10.5px; line-height:1.6; }

    .legend { display:flex; flex-direction:column; gap:8px; }
    .legend-row { display:grid; grid-template-columns:32px 1fr 50px; align-items:center; gap:10px; }
    .legend-swatch { height: 9px; border-radius:999px; box-shadow:inset 0 1px 0 rgba(255,255,255,.45); }
    .legend-row b { font-size:9.7px; }
    .legend-row span { color:var(--muted); font-size:9px; text-align:right; }

    .calendar-list { max-height: 260px; overflow:auto; padding-right:4px; }
    .calendar-list::-webkit-scrollbar { width:6px; }
    .calendar-list::-webkit-scrollbar-thumb { background:rgba(29,29,31,.10); border-radius:999px; }
    .calendar-row {
      display:grid; grid-template-columns:46px 1fr auto;
      gap:9px; align-items:center;
      padding:9px 8px; border-radius:11px;
      cursor: pointer;
    }
    .calendar-row.active { background:rgba(29,29,31,.035); }
    .calendar-row strong { font-size:9.8px; }
    .calendar-row span { color:var(--muted); font-size:8.8px; }
    .calendar-row.future strong { color: var(--muted-2); font-weight: 600; }
    .calendar-row.future span { color: var(--muted-2); }
    .calendar-status { width:7px; height:7px; border-radius:50%; background:#c0c0c5; }
    .calendar-status.done { background:var(--orange); box-shadow:0 0 0 4px rgba(200,115,53,.09); }
    .calendar-status.current { background:var(--green); box-shadow:0 0 0 4px rgba(31,157,104,.09); }
    .calendar-status.future { background:var(--danger); box-shadow:0 0 0 4px rgba(200,63,87,.09); }

    .footer {
      margin-top: 18px;
      display:flex; flex-direction:column; gap:10px;
      color:var(--muted-2); font-size:9.4px; padding: 4px 8px;
    }
    .footer-row { display:flex; justify-content:space-between; gap:24px; align-items:center; }
    .footer strong { color:var(--muted); }
    .footer-links { text-align:center; font-size:10.5px; }
    .footer-links a { color:var(--muted); text-decoration:none; font-weight:650; }
    .footer-links a:hover { color:var(--purple); text-decoration:underline; }
    .footer-credits { text-align:center; padding-top:8px; border-top:1px solid rgba(29,29,31,.06); }
    .footer-credits a { color:var(--muted); text-decoration:none; }
    .footer-credits a:hover { color:var(--purple); text-decoration:underline; }

    .chip {
      display:inline-flex; align-items:center; gap:6px;
      padding: 5px 8px; border-radius:999px; font-size:8.7px; font-weight:650;
      background:rgba(95,37,159,.055); color:#6b3a91; border:1px solid rgba(95,37,159,.08);
    }

    .tooltip { position:relative; }
    .tooltip:hover::after {
      content: attr(data-tip);
      position:absolute; left:50%; bottom: calc(100% + 8px); transform:translateX(-50%);
      width: 190px; padding:8px 9px; border-radius:11px;
      background:rgba(255,255,255,.94); border:1px solid rgba(29,29,31,.07);
      color:#4b4b50; font-size:9px; line-height:1.4; z-index:100;
      box-shadow:0 14px 32px rgba(29,29,31,.12);
      backdrop-filter:blur(18px) saturate(150%);
    }

    @media (max-width: 1350px) {
      .app-shell { width: 1220px; }
      .main-grid { grid-template-columns: 860px 342px; }
      .strategy-card p { display:none; }
      .strategy-card { min-height:84px; }
    }

    /* True mobile layout, not a shrunk desktop one. Every fixed px width
       above (body's 1180px floor, the 365px countdown chip, 126px player
       cards, the 800px+/1220px shell widths) is a real overflow source on
       a ~360-430px phone -- this block re-lays out from scratch rather
       than nudging desktop values down. */
    @media (max-width: 640px) {
      html, body { overflow-x: hidden; }
      body { min-width: 0; width: 100%; }
      .app-shell { width: 100%; margin: 12px auto; padding: 0 12px; }

      .topbar {
        grid-template-columns: 1fr auto;
        row-gap: 10px;
        padding: 12px;
        position: static; /* sticky + a tall wrapped header eats too much viewport on a phone */
      }
      .brand { min-width: 0; }
      .brand h1 { font-size: 16px; }
      .segmented, .countdown-chip { grid-column: 1 / -1; width: 100%; min-width: 0; }
      .segmented { flex-wrap: wrap; }
      .countdown-chip { flex-wrap: wrap; row-gap: 8px; }
      .countdown-meta { min-width: 0; flex: 1 1 auto; }
      .time-cells { margin-left: 0; }
      .time-cell { width: 34px; min-height: 36px; }

      .strategy-strip { grid-template-columns: repeat(2, 1fr); }
      .strategy-card { min-height: 64px; padding: 10px 11px; }

      .main-grid { grid-template-columns: 1fr; }
      .panel-head { flex-wrap: wrap; padding: 16px; }
      .head-actions { flex-wrap: wrap; }

      .gw-summary { grid-template-columns: repeat(2, 1fr); padding: 12px; }

      .pitch { margin: 10px; min-height: 0; }
      .squad { padding: 20px 8px; min-height: 0; }
      .position-row { gap: 8px; }
      .player-card { width: 92px; }
      .player-visual { height: 68px; }
      .player-photo { width: 60px; height: 68px; }
      .avatar { width: 46px; height: 46px; font-size: 14px; margin-bottom: 4px; }
      .player-name { font-size: 9px; }
      .player-sub { font-size: 7px; gap: 3px; }
      .badge { min-width: 19px; height: 19px; font-size: 7.5px; }

      .bench-row { gap: 10px; }

      .side-card, .info-card { padding: 14px; }
      .info-grid { grid-template-columns: 1fr; }
      .strategy-detail { padding: 12px; }

      .footer-row { flex-direction: column; align-items: flex-start; gap: 8px; }
    }
  </style>
</head>
<body>
  <main class="app-shell">
    <header class="topbar glass">
      <div class="brand">
        <div class="brand-mark">⚽</div>
        <div>
          <h1>PLFantasyBot</h1>
          <p id="brandSubtitle">—</p>
        </div>
      </div>

      <div class="segmented" id="seasonToggle" aria-label="Season selection"></div>

      <div class="countdown-chip" id="countdownChip">
        <div class="dot"></div>
        <div class="countdown-meta">
          <strong id="deadlineTitle">Deadline</strong>
          <span id="deadlineLabel">—</span>
        </div>
        <div class="time-cells">
          <div class="time-cell"><b id="cdDays">00</b><small>Days</small></div>
          <div class="time-cell"><b id="cdHours">00</b><small>Hrs</small></div>
          <div class="time-cell"><b id="cdMins">00</b><small>Min</small></div>
          <div class="time-cell"><b id="cdSecs">00</b><small>Sec</small></div>
        </div>
      </div>

      <button class="ghost-btn" id="infoToggle" aria-label="About this dashboard" title="About this dashboard">ⓘ</button>
    </header>

    <section class="strategy-strip" id="strategyStrip"></section>

    <section class="info-panel" id="infoPanel" style="display:none">
      <div class="info-card glass">
        <h2>What this is</h2>
        <p>PLFantasyBot predicts how many points every Premier League player will score using a model trained on past seasons' results, then builds a legal 15-man squad under FPL's actual budget and formation rules. Rather than committing to one approach, it runs five different squad-building policies side by side every gameweek, so you can compare how they'd each play it before deciding what to actually do with your real team.</p>
        <p>Nothing here submits anything automatically. It's a decision-support dashboard — you look at the picks, then make the transfer yourself in the FPL app.</p>
      </div>

      <div class="info-card glass">
        <h2>The five strategies</h2>
        <p>All five share the exact same underlying model and feature set — they differ only in policy: how many -4 hits they're willing to take in a week, how far ahead they plan the fixture list, and (for Differential) how strongly they favour low-ownership picks over the obvious template choice.</p>
        <div class="info-grid" id="infoStrategyGrid"></div>
      </div>

      <div class="info-card glass">
        <h2>Reading a squad</h2>
        <div class="info-grid">
          <div class="info-item"><div><strong>Season toggle</strong>2026-27 Live shows real picks for the upcoming gameweek. 2025-26 Backtest is a full validated season simulation — the model never saw that season's results while training, so it's a fair test of how each strategy would actually have performed.</div></div>
          <div class="info-item"><div><strong>GW score / Season total</strong>Points scored that gameweek, and the running total across the season so far. Shows "—" until that gameweek has actually been played.</div></div>
          <div class="info-item"><div><strong>Transfers / Hits</strong>How many changes were made that week, and the points cost of any beyond the free allowance (each extra transfer costs -4).</div></div>
          <div class="info-item"><div><strong>Chip badge</strong>Wildcard (rebuild the squad for free), Bench Boost (bench points count too), Triple Captain (captain scores 3x instead of 2x), or Free Hit (one-week-only squad, reverts next gameweek) — shown when one was played that week.</div></div>
        </div>
      </div>

      <div class="info-card glass">
        <h2>Player cards</h2>
        <div class="info-grid">
          <div class="info-item"><div><strong>C / V / 3× / C*</strong>Captain (2x points), Vice-captain (backup), Triple Captain (3x points), and Effective Captain (C* — the vice took over because the real captain didn't play that week).</div></div>
          <div class="info-item"><div><strong>Fixture difficulty</strong>FPL's own 1 (easiest) to 5 (hardest) rating for that gameweek's opponent, colour-coded green through red on the small square next to the opponent's code.</div></div>
          <div class="info-item"><div><strong>Ownership %</strong>The share of all FPL managers who currently own that player — what the Differential strategy specifically leans against.</div></div>
          <div class="info-item"><div><strong>Availability badge</strong>100% (neutral) means no fitness concern on record. A percentage means FPL's own graded doubtful/injured probability (e.g. 75%). Letter codes — S (suspended), U (transferred/left the club), N (not in the matchday squad) — cover situations that aren't really a probability at all. Hover any badge for the underlying news text.</div></div>
        </div>
      </div>

      <div class="info-card glass">
        <h2>Season calendar & deadlines</h2>
        <div class="info-grid">
          <div class="info-item"><div><strong>Calendar dots</strong><span class="legend-swatch" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--orange);margin-right:5px;"></span>amber = already happened,
            <span class="legend-swatch" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin:0 5px 0 8px;"></span>green = the current gameweek,
            <span class="legend-swatch" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--danger);margin:0 5px 0 8px;"></span>red = still to come. Click any row to point the countdown at that gameweek instead.</div></div>
          <div class="info-item"><div><strong>Deadline countdown</strong>Always shown in your own browser's local time zone, labelled explicitly so it's never ambiguous. Turns amber under 24 hours out, grey once the deadline has passed.</div></div>
        </div>
      </div>

      <div class="info-card glass">
        <h2>Data & disclaimer</h2>
        <p>Player and fixture data comes from the official Fantasy Premier League API, refreshed automatically every 30 minutes. Player photos are Premier League imagery where available. This is an analytical tool built for research and personal use — not real-money betting or financial advice, and not affiliated with the Premier League or FPL.</p>
      </div>
    </section>

    <section class="info-panel" id="positionView" style="display:none">
      <div class="info-card glass">
        <h2>Balanced — full squad by position</h2>
        <p id="positionViewSubtitle">—</p>
      </div>
      <div class="info-card glass" style="background:linear-gradient(180deg, var(--pitch-1), var(--pitch-2));">
        <div id="positionViewGroups"></div>
      </div>
    </section>

    <section class="info-panel" id="adminView" style="display:none">
      <div class="info-card glass">
        <h2>Admin — gameweek status</h2>
        <p>Read-only here for now. Toggling "done" (which stops deadline-reminder emails for that gameweek) is done via GitHub Actions &rarr; <strong>Mark gameweek done</strong> until a password-gated write backend is wired up.</p>
      </div>
      <div class="info-card glass">
        <div id="adminStatusList"></div>
      </div>
    </section>

    <section class="main-grid" id="mainGrid">
      <article class="pitch-panel">
        <div class="panel-head">
          <div class="head-copy">
            <div style="display:flex; align-items:center; gap:8px;">
              <h2 id="gwHeading">—</h2>
              <span class="chip" id="chipPlayed">No chip</span>
            </div>
            <p id="gwSubheading">—</p>
          </div>
          <div class="head-actions">
            <button class="ghost-btn" id="prevGw" aria-label="Previous gameweek">←</button>
            <div class="select-shell"><select id="gwSelect" aria-label="Choose gameweek"></select></div>
            <button class="ghost-btn" id="nextGw" aria-label="Next gameweek">→</button>
          </div>
        </div>

        <div class="gw-summary">
          <div class="metric"><label>GW score</label><strong id="gwScore">—</strong></div>
          <div class="metric"><label>Season total</label><strong id="seasonTotal" class="accent">—</strong></div>
          <div class="metric"><label>Transfers</label><strong id="transfers">—</strong></div>
          <div class="metric"><label>Hits</label><strong id="hits">—</strong></div>
          <div class="metric"><label>Status</label><strong id="gwStatus">—</strong></div>
        </div>

        <div class="pitch">
          <div class="halfway-line"></div>
          <div class="center-spot"></div>
          <div class="penalty top"></div><div class="six-yard top"></div><div class="goal top"></div><div class="penalty-spot top"></div><div class="arc top"></div>
          <div class="penalty bottom"></div><div class="six-yard bottom"></div><div class="goal bottom"></div><div class="penalty-spot bottom"></div><div class="arc bottom"></div>
          <div class="squad" id="squad"></div>
        </div>

        <div class="bench-wrap">
          <div class="bench-title"><strong>Bench</strong><span>Substitution priority →</span></div>
          <div class="bench-row" id="bench"></div>
        </div>
      </article>

      <aside class="sidebar">
        <section class="side-card glass">
          <div class="side-head"><h3>Strategy leaderboard</h3><span id="leaderboardSeason">—</span></div>
          <div id="leaderboard"></div>
        </section>

        <section class="side-card glass">
          <div class="side-head"><h3>Selected strategy</h3><span id="riskText">—</span></div>
          <div class="strategy-detail">
            <h4 id="detailTitle">—</h4>
            <p id="detailDescription"></p>
          </div>
        </section>

        <section class="side-card glass">
          <div class="side-head"><h3>Fixture difficulty</h3><span>FPL scale</span></div>
          <div class="legend">
            <div class="legend-row"><div class="legend-swatch fdr-1"></div><b>1 · Easy</b><span>Best</span></div>
            <div class="legend-row"><div class="legend-swatch fdr-2"></div><b>2</b><span>Good</span></div>
            <div class="legend-row"><div class="legend-swatch fdr-3"></div><b>3</b><span>Neutral</span></div>
            <div class="legend-row"><div class="legend-swatch fdr-4"></div><b>4</b><span>Hard</span></div>
            <div class="legend-row"><div class="legend-swatch fdr-5"></div><b>5 · Hard</b><span>Toughest</span></div>
          </div>
        </section>

        <section class="side-card glass">
          <div class="side-head"><h3>Season calendar</h3><span id="calendarSpan">All gameweeks</span></div>
          <div class="calendar-list" id="calendarList"></div>
        </section>
      </aside>
    </section>

    <footer class="footer">
      <div class="footer-row">
        <div><strong>Data:</strong> official Fantasy Premier League feeds / Premier League player imagery where available.</div>
        <div>PLFantasyBot is an analytical tool. It is not real-money betting or financial advice.</div>
      </div>
      <div class="footer-links">
        <a href="#" id="positionViewLink">See position view</a> · <a href="#" id="adminViewLink">See admin page</a>
      </div>
      <div class="footer-credits">
        Built by <a href="https://github.com/kayanshah" target="_blank" rel="noopener">Kayan Shah</a> and <a href="https://github.com/5H41L3N" target="_blank" rel="noopener">Shailen Patel</a> · <a href="https://github.com/KayanShah/PLFantasyBot2026-27" target="_blank" rel="noopener">Link to Repository</a>
      </div>
    </footer>
  </main>

<script id="site-data" type="application/json">__SITE_DATA__</script>
<script>
  const DATA = JSON.parse(document.getElementById('site-data').textContent);

  // Approximate 2026-27 Premier League club colours, keyed on the same
  // short codes already used for opponents. Falls back to a neutral grey
  // for anything unrecognised rather than failing to render.
  const CLUBS = {
    ARS:['#ef0107','#9b0004'], AVL:['#95bfe5','#670e36'], BOU:['#d71920','#1a1a1a'],
    BRE:['#e30613','#0d0d0d'], BHA:['#0057b8','#ffcd00'], CHE:['#034694','#001b3a'],
    COV:['#78d0f2','#1b1b1b'], CRY:['#1b458f','#c4122e'], EVE:['#003399','#001b57'],
    FUL:['#111111','#cc0000'], HUL:['#f18a01','#1a1a1a'], IPS:['#3a64a3','#dc1e35'],
    LEE:['#e8e8ea','#1d428a'], LIV:['#e31b23','#7c1015'], MCI:['#6cabdd','#296b9d'],
    MUN:['#da291c','#8d1114'], NEW:['#1a1a1a','#57a0d3'], NFO:['#dd0000','#8f0000'],
    TOT:['#e8e8ea','#132257'], SUN:['#eb172b','#1a1a1a'],
  };

  const POSITION_ORDER = ['FWD','MID','DEF','GKP'];

  let state = { seasonIdx: 0, strategyIdx: 0, gwIdx: 0, view: 'dashboard' };
  let currentDeadlineISO = null;
  let currentDeadlineGW = null;

  function el(id){ return document.getElementById(id); }
  function currentSeason(){ return DATA.seasons[state.seasonIdx]; }
  function currentStrategy(){ return currentSeason().strategies[state.strategyIdx]; }
  function currentGwList(){ return currentStrategy().gameweeks; }
  function currentGw(){
    const gws = currentGwList();
    if (state.gwIdx >= gws.length) state.gwIdx = gws.length - 1;
    if (state.gwIdx < 0) state.gwIdx = 0;
    return gws[state.gwIdx];
  }

  function renderSeasonToggle(){
    el('seasonToggle').innerHTML = DATA.seasons.map((s,i) =>
      `<button class="seg-btn ${i===state.seasonIdx?'active':''}" data-season="${i}">${s.label}</button>`
    ).join('');
    document.querySelectorAll('[data-season]').forEach(btn => btn.onclick = () => {
      state.seasonIdx = Number(btn.dataset.season);
      state.strategyIdx = 0; state.gwIdx = 0;
      renderAll();
    });
  }

  function renderStrategies(){
    const strategies = currentSeason().strategies;
    el('strategyStrip').innerHTML = strategies.map((s,i) => `
      <button class="strategy-card glass ${i===state.strategyIdx?'active':''}" data-strategy="${i}" style="text-align:left;color:inherit;">
        <div class="strategy-top">
          <div><h3>${s.label}</h3><p>${s.short}</p></div>
          <span class="risk-pill risk-${s.risk_tier}">${s.risk}</span>
        </div>
      </button>`).join('');
    document.querySelectorAll('[data-strategy]').forEach(btn => btn.onclick = () => {
      state.strategyIdx = Number(btn.dataset.strategy);
      state.gwIdx = 0;
      renderAll();
    });
  }

  function initials(name){ return (name || '?').split(' ').map(x => x[0]).slice(0,2).join(''); }

  function statusLabel(p){
    if (!p.status) {
      // No flag at all -- FPL doesn't publish an explicit 100 for fully fit
      // players (chance_of_playing is null, not 100), but "no concern" reads
      // as 100% to a manager, so show it explicitly rather than leaving a
      // blank most viewers would just read as "no data."
      return '<span class="badge status fit tooltip" data-tip="Fully available">100%</span>';
    }
    const map = {i:'!', s:'S', d:'?', u:'U', n:'N'};
    // Doubtful/injured carry a real graded percentage worth showing on the
    // badge itself -- suspended/unavailable/not-in-squad are binary (can't
    // play, full stop), where a letter code says more than "0%" would.
    const showPercent = (p.status === 'd' || p.status === 'i') && p.chance_of_playing != null;
    const label = showPercent ? `${p.chance_of_playing}%` : (map[p.status] || '!');
    const tip = (p.news || 'Player availability flag').replace(/"/g, '&quot;');
    return `<span class="badge status tooltip" data-tip="${tip}">${label}</span>`;
  }

  function playerCard(p, notPlayedYet){
    const [a,b] = CLUBS[p.team] || ['#59637c','#2c3244'];
    const c = p.is_triple_captain ? '<span class="badge captain">3×</span>' : p.is_captain ? '<span class="badge captain">C</span>' : '';
    const v = p.is_vice_captain ? '<span class="badge vice">V</span>' : '';
    const effective = p.is_effective_captain && !p.is_captain ? '<span class="badge captain">C*</span>' : '';
    const initialsText = initials(p.name);
    const photo = p.photo_code
      ? `<img class="player-photo" src="https://resources.premierleague.com/premierleague/photos/players/110x140/p${p.photo_code}.png" alt="${p.name}" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'avatar',textContent:'${initialsText}'}))">`
      : `<div class="avatar">${initialsText}</div>`;
    const pointsDisplay = notPlayedYet ? '–' : (p.played ? p.points : '—');
    const ownershipDisplay = (p.ownership != null) ? p.ownership.toFixed(1) : '–';
    return `<div class="player-card" style="--club-a:${a};--club-b:${b};">
      <div class="player-visual">
        <div class="badges">${c}${effective}${v}${statusLabel(p)}</div>
        ${photo}
      </div>
      <div class="player-info">
        <div class="player-name" title="${p.name}">${p.name}</div>
        <div class="player-sub"><span>${p.team}</span><span>·</span><span>${p.opponent}</span><span class="fdr fdr-${p.difficulty||3}">${p.difficulty ?? '–'}</span><span class="ownership">${ownershipDisplay}%</span><span class="points">${pointsDisplay}</span></div>
      </div>
    </div>`;
  }

  function renderSquad(){
    const gw = currentGw();
    const notPlayedYet = gw.season_total == null;
    el('squad').innerHTML = POSITION_ORDER.map(pos =>
      `<div class="position-row">${gw.starting_xi.filter(p => p.position === pos).map(p => playerCard(p, notPlayedYet)).join('')}</div>`
    ).join('');
    el('bench').innerHTML = gw.bench.map(p => playerCard(p, notPlayedYet)).join('');
  }

  function renderLeaderboard(){
    const season = currentSeason();
    const items = season.strategies.map((s,i) => {
      const gws = s.gameweeks;
      const last = gws.length ? gws[gws.length - 1] : null;
      const score = (last && last.season_total != null) ? last.season_total : null;
      return { i, label: s.label, risk: s.risk, score };
    }).sort((x,y) => (y.score ?? -1) - (x.score ?? -1));
    el('leaderboard').innerHTML = items.map((s,rank) => `<div class="leader-row ${s.i===state.strategyIdx?'active':''}" data-strategy="${s.i}">
      <div class="rank">#${rank+1}</div>
      <div class="leader-name">${s.label}<small>${s.risk}</small></div>
      <div class="leader-score">${s.score == null ? '—' : s.score + ' pts'}</div>
    </div>`).join('');
    el('leaderboardSeason').textContent = season.label;
    document.querySelectorAll('#leaderboard [data-strategy]').forEach(row => row.onclick = () => {
      state.strategyIdx = Number(row.dataset.strategy);
      state.gwIdx = 0;
      renderAll();
    });
  }

  function renderDetails(){
    const s = currentStrategy();
    const season = currentSeason();
    const gw = currentGw();
    const notPlayed = gw.season_total == null;

    el('brandSubtitle').textContent = `${season.label} · ${s.label} strategy`;
    el('detailTitle').textContent = s.label;
    el('detailDescription').textContent = s.description;
    el('riskText').textContent = s.risk;

    el('seasonTotal').textContent = notPlayed ? '—' : gw.season_total;
    el('gwScore').textContent = notPlayed ? '—' : gw.gw_score;
    el('transfers').textContent = gw.transfers != null ? gw.transfers : '—';
    el('hits').textContent = gw.hits ? `-${gw.hits * 4}` : '0';
    el('chipPlayed').textContent = gw.chip || 'No chip';
    el('gwStatus').textContent = notPlayed ? 'Not yet played' : 'Finished';
    el('gwSubheading').textContent = notPlayed
      ? 'Live squad · team locks at the deadline'
      : (season.kind === 'backtest' ? 'Backtest result · finalised' : 'Result locked in');
    el('gwHeading').textContent = `Gameweek ${gw.gw}`;
  }

  function renderGwSelect(){
    const gws = currentGwList();
    el('gwSelect').innerHTML = gws.map((g,i) =>
      `<option value="${i}" ${i===state.gwIdx?'selected':''}>Gameweek ${g.gw}${g.chip ? ' — ' + g.chip : ''}${g.season_total==null ? ' (not yet played)' : ''}</option>`
    ).join('');
    el('gwSelect').onchange = e => { state.gwIdx = Number(e.target.value); renderPanel(); };
    el('prevGw').onclick = () => { if (state.gwIdx > 0) { state.gwIdx--; renderPanel(); } };
    el('nextGw').onclick = () => { if (state.gwIdx < gws.length - 1) { state.gwIdx++; renderPanel(); } };
  }

  function renderPanel(){
    renderDetails();
    renderSquad();
    el('gwSelect').value = String(state.gwIdx);
    el('prevGw').disabled = state.gwIdx === 0;
    el('nextGw').disabled = state.gwIdx === currentGwList().length - 1;
  }

  function formatDeadline(iso){
    // timeZoneName makes explicit this is the viewer's local time, not UTC.
    return new Date(iso).toLocaleString(undefined, {
      weekday: 'short', day: 'numeric', month: 'short',
      hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
    });
  }

  function renderCalendarAndCountdown(){
    const season = currentSeason();
    const calendar = season.gameweek_calendar;
    const hasCalendar = !!(calendar && calendar.length);

    el('countdownChip').style.display = hasCalendar ? '' : 'none';
    el('calendarList').closest('.side-card').style.display = hasCalendar ? '' : 'none';
    if (!hasCalendar) { currentDeadlineISO = null; return; }

    if (!currentDeadlineISO) {
      const upcoming = calendar.find(g => !g.finished) || calendar[0];
      currentDeadlineGW = upcoming.gw;
      currentDeadlineISO = upcoming.deadline;
    }

    // "Current" is a fixed fact about the calendar (the next gameweek that
    // hasn't happened yet) -- not tied to whichever row the viewer has
    // clicked to browse. Dot colour reflects true past/now/future status;
    // only the row highlight ("active") follows the click.
    const nextGw = (calendar.find(g => !g.finished) || calendar[0]).gw;

    el('calendarList').innerHTML = calendar.map(g => {
      const isSelected = g.gw === currentDeadlineGW;
      const isCurrent = g.gw === nextGw;
      const status = g.finished ? 'done' : (isCurrent ? 'current' : 'future');
      const future = !g.finished && !isCurrent;
      return `<div class="calendar-row ${isSelected?'active':''} ${future?'future':''}" data-gw="${g.gw}">
        <strong>GW ${g.gw}</strong><span>${formatDeadline(g.deadline)}</span><i class="calendar-status ${status}"></i>
      </div>`;
    }).join('');

    document.querySelectorAll('#calendarList [data-gw]').forEach(row => row.onclick = () => {
      const picked = calendar.find(g => g.gw === Number(row.dataset.gw));
      if (!picked) return;
      currentDeadlineGW = picked.gw;
      currentDeadlineISO = picked.deadline;
      tickDeadline();
      renderCalendarAndCountdown();
    });

    tickDeadline();
  }

  function tickDeadline(){
    if (!currentDeadlineISO) return;
    const deadline = new Date(currentDeadlineISO);
    let diff = deadline - new Date();
    const chip = el('countdownChip');
    chip.classList.remove('urgent', 'passed');
    if (diff <= 0) { chip.classList.add('passed'); diff = 0; }
    else if (diff < 24 * 60 * 60 * 1000) { chip.classList.add('urgent'); }

    const days = Math.floor(diff / 86400000); diff %= 86400000;
    const hrs = Math.floor(diff / 3600000); diff %= 3600000;
    const mins = Math.floor(diff / 60000); diff %= 60000;
    const secs = Math.floor(diff / 1000);
    el('cdDays').textContent = String(days).padStart(2,'0');
    el('cdHours').textContent = String(hrs).padStart(2,'0');
    el('cdMins').textContent = String(mins).padStart(2,'0');
    el('cdSecs').textContent = String(secs).padStart(2,'0');
    el('deadlineTitle').textContent = `GW ${currentDeadlineGW} deadline`;
    el('deadlineLabel').textContent = formatDeadline(currentDeadlineISO);
  }

  function renderInfoPanel(){
    // Strategy metadata (label/description/risk) is identical across
    // seasons, so any season's list works here -- this doesn't need to
    // re-render on navigation, only once.
    const strategies = DATA.seasons[0].strategies;
    el('infoStrategyGrid').innerHTML = strategies.map(s => `
      <div class="info-strategy-card">
        <div class="strategy-top">
          <h3>${s.label}</h3>
          <span class="risk-pill risk-${s.risk_tier}">${s.risk}</span>
        </div>
        <p>${s.description}</p>
      </div>`).join('');
  }

  function renderPositionView(){
    // The Balanced flagship's most recent squad -- live if it exists (the
    // whole point is "what does the team look like right now"), otherwise
    // whatever season is available. Grouped by how many of each position
    // the squad actually holds (2/5/5/3), not by starting XI/bench or by
    // the pitch's own GKP-last display convention -- this is a squad list,
    // not a formation view, so GKP reads first here.
    const season = DATA.seasons.find(s => s.kind === 'live') || DATA.seasons[0];
    const balanced = season.strategies.find(s => s.key === 'balanced') || season.strategies[0];
    const gws = balanced.gameweeks;
    const latest = gws[gws.length - 1];
    const notPlayedYet = latest.season_total == null;
    const allPlayers = [...latest.starting_xi, ...latest.bench];

    el('positionViewSubtitle').textContent =
      `${season.label} · Gameweek ${latest.gw} · every player currently in the squad, grouped by position.`;

    el('positionViewGroups').innerHTML = ['GKP', 'DEF', 'MID', 'FWD'].map(pos => {
      const players = allPlayers.filter(p => p.position === pos);
      return `
        <div style="margin-bottom:18px;">
          <div style="font-size:11px;font-weight:700;color:#183f2b;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px 4px;">${pos} (${players.length})</div>
          <div class="position-row">${players.map(p => playerCard(p, notPlayedYet)).join('')}</div>
        </div>`;
    }).join('');
  }

  function renderAdminView(){
    const status = DATA.adminStatus || {};
    const gws = Object.keys(status).map(Number).sort((a, b) => a - b);
    if (!gws.length) {
      el('adminStatusList').innerHTML = '<p style="color:var(--muted);font-size:11.5px;">No gameweeks marked yet.</p>';
      return;
    }
    el('adminStatusList').innerHTML = gws.map(gw => {
      const s = status[gw];
      const pill = s.done
        ? '<span class="risk-pill risk-low">Done</span>'
        : '<span class="risk-pill risk-high">Not done</span>';
      const notes = s.notes ? `<p>${s.notes}</p>` : '';
      const when = s.marked_at ? `<p style="color:var(--muted-2);font-size:9.5px;">Marked ${formatDeadline(s.marked_at)}</p>` : '';
      return `<div class="info-strategy-card" style="margin-bottom:10px;">
        <div class="strategy-top"><h3>Gameweek ${gw}</h3>${pill}</div>
        ${notes}${when}
      </div>`;
    }).join('');
  }

  function applyView(){
    const view = state.view;
    el('infoPanel').style.display = view === 'info' ? '' : 'none';
    el('positionView').style.display = view === 'position' ? '' : 'none';
    el('adminView').style.display = view === 'admin' ? '' : 'none';
    const showDashboard = view === 'dashboard';
    el('strategyStrip').style.display = showDashboard ? '' : 'none';
    el('mainGrid').style.display = showDashboard ? '' : 'none';
    el('infoToggle').textContent = view === 'info' ? '✕' : 'ⓘ';
    el('infoToggle').title = view === 'info' ? 'Back to dashboard' : 'About this dashboard';
  }

  el('infoToggle').onclick = () => {
    state.view = state.view === 'info' ? 'dashboard' : 'info';
    applyView();
  };

  el('positionViewLink').onclick = (e) => {
    e.preventDefault();
    state.view = state.view === 'position' ? 'dashboard' : 'position';
    applyView();
  };

  el('adminViewLink').onclick = (e) => {
    e.preventDefault();
    state.view = state.view === 'admin' ? 'dashboard' : 'admin';
    applyView();
  };

  function renderAll(){
    currentDeadlineISO = null; // re-default to the next upcoming deadline on season/strategy switch
    renderSeasonToggle();
    renderStrategies();
    renderLeaderboard();
    renderGwSelect();
    renderPanel();
    renderCalendarAndCountdown();
  }

  renderInfoPanel();
  renderPositionView();
  renderAdminView();
  applyView();
  renderAll();
  setInterval(tickDeadline, 1000);
</script>
</body>
</html>
"""


def load_season_block(season: str, kind: str, label: str, shortcodes: dict[str, str]) -> dict | None:
    manifest_path = DATA_DIR / f"strategies_manifest_{season}.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    strategies = []
    for entry in sorted(manifest["strategies"], key=lambda e: e["order"]):
        squads_path = DATA_DIR / entry["squads_file"]
        squads = json.loads(squads_path.read_text(encoding="utf-8"))
        gameweeks = apply_shortcodes(squads["gameweeks"], shortcodes)
        strategies.append({**entry, "gameweeks": gameweeks})

    block = {"key": season, "kind": kind, "label": label, "strategies": strategies}

    calendar_path = DATA_DIR / "live_gameweek_calendar.json"
    if kind == "live" and calendar_path.exists():
        block["gameweek_calendar"] = json.loads(calendar_path.read_text(encoding="utf-8"))

    return block


def main() -> None:
    shortcodes = build_team_shortcodes()

    seasons = []
    for season, kind, label in SEASON_MANIFESTS:
        block = load_season_block(season, kind, label, shortcodes)
        if block is not None:
            seasons.append(block)

    if not seasons:
        raise SystemExit(
            "No strategies_manifest_*.json found in data/ -- run "
            "model/run_all_strategies.py (and/or model/generate_live_strategies.py) first."
        )

    admin_status_path = DATA_DIR / "admin_status.json"
    admin_status = (
        json.loads(admin_status_path.read_text(encoding="utf-8")) if admin_status_path.exists() else {}
    )

    site_data = {"seasons": seasons, "adminStatus": admin_status}
    html = TEMPLATE.replace("__SITE_DATA__", json.dumps(site_data))
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Built {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")
    for s in seasons:
        print(f"  {s['label']}: {len(s['strategies'])} strategies")


if __name__ == "__main__":
    main()
