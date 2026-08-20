"""
Emails a deadline reminder at 2 days, 1 day, 12 hours, and 1 hour before the
next unplayed gameweek's deadline, showing the Balanced strategy's current
recommended squad -- the only strategy anyone would actually act on.

Idempotent by design: each window (2d/1d/12h/1h) is sent at most once per
gameweek. `data/reminders_sent.json` tracks which windows have already gone
out; a run inside a window that's already been sent is a no-op, so this can
run on the same 30-minute cron as everything else without spamming.

Requires (as GitHub repository secrets -- never in code or committed files):
    RESEND_API_KEY    Resend.com API key
    ALERT_FROM_EMAIL  verified sender address
    ALERT_TO_EMAIL    comma-separated recipient list

Usage:
    python model/send_deadline_reminder.py            # send if due
    python model/send_deadline_reminder.py --force     # ignore the sent-log, send whichever window matches now
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CALENDAR_PATH = DATA_DIR / "live_gameweek_calendar.json"
SQUAD_PATH = DATA_DIR / "live_squads_balanced.json"
SENT_LOG_PATH = DATA_DIR / "reminders_sent.json"
SITE_URL = "https://plfantasybot2026-27.vercel.app"

RESEND_API = "https://api.resend.com/emails"

# (window key, timedelta before deadline, human label). A run is "due" for a
# window once now is within WINDOW_SLOP of that mark -- wide enough that a
# 30-minute cron always lands inside it at least once.
WINDOWS = [
    ("2d", timedelta(days=2), "2 days"),
    ("1d", timedelta(days=1), "1 day"),
    ("12h", timedelta(hours=12), "12 hours"),
    ("1h", timedelta(hours=1), "1 hour"),
]
WINDOW_SLOP = timedelta(minutes=35)

POSITION_ORDER = ["GKP", "DEF", "MID", "FWD"]


def load_sent_log() -> dict:
    return json.loads(SENT_LOG_PATH.read_text(encoding="utf-8")) if SENT_LOG_PATH.exists() else {}


def save_sent_log(log: dict) -> None:
    SENT_LOG_PATH.write_text(json.dumps(log, indent=2), encoding="utf-8")


def next_deadline() -> dict | None:
    calendar = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    upcoming = [g for g in calendar if not g["finished"]]
    return upcoming[0] if upcoming else None


def due_window(deadline: datetime, now: datetime, already_sent: list[str]) -> tuple[str, str] | None:
    for key, delta, label in WINDOWS:
        if key in already_sent:
            continue
        mark = deadline - delta
        if mark - WINDOW_SLOP <= now <= mark + WINDOW_SLOP:
            return key, label
    return None


def format_deadline(deadline: datetime) -> str:
    return deadline.strftime("%A %d %B, %H:%M UTC")


def squad_rows_html(players: list[dict]) -> str:
    rows = []
    for p in players:
        badge = ""
        if p.get("is_triple_captain"):
            badge = " (TC)"
        elif p.get("is_captain"):
            badge = " (C)"
        elif p.get("is_vice_captain"):
            badge = " (VC)"
        flag = ""
        if p.get("status"):
            pct = f" {p['chance_of_playing']}%" if p.get("chance_of_playing") is not None else ""
            flag = f' <span style="color:#c83f57;">[{p["status"].upper()}{pct}]</span>'
        rows.append(
            f'<tr><td style="padding:4px 10px;">{p["position"]}</td>'
            f'<td style="padding:4px 10px;">{p["name"]}{badge}{flag}</td>'
            f'<td style="padding:4px 10px;color:#6e6e73;">{p["team"]} vs {p["opponent"]}</td></tr>'
        )
    return "".join(rows)


def build_email_html(gw: int, deadline: datetime, window_label: str, squad: dict | None) -> str:
    deadline_str = format_deadline(deadline)
    if squad is None:
        squad_html = "<p>No squad data available -- run <code>generate_live_strategies.py</code> first.</p>"
    else:
        xi = squad["gameweeks"][0]["starting_xi"]
        bench = squad["gameweeks"][0]["bench"]
        xi_sorted = sorted(xi, key=lambda p: POSITION_ORDER.index(p["position"]))
        squad_html = f"""
        <h3 style="margin:20px 0 6px;">Starting XI</h3>
        <table style="border-collapse:collapse;width:100%;font-size:14px;">{squad_rows_html(xi_sorted)}</table>
        <h3 style="margin:20px 0 6px;">Bench</h3>
        <table style="border-collapse:collapse;width:100%;font-size:14px;">{squad_rows_html(bench)}</table>
        """

    return f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:600px;margin:0 auto;color:#1d1d1f;">
      <div style="background:linear-gradient(90deg,#38003c,#5e0067);color:white;padding:20px 24px;border-radius:14px 14px 0 0;">
        <h1 style="margin:0;font-size:20px;">PLFantasyBot — GW{gw} deadline in {window_label}</h1>
        <p style="margin:8px 0 0;opacity:0.9;font-size:14px;">Deadline: {deadline_str}</p>
      </div>
      <div style="border:1px solid #e5e5ea;border-top:none;padding:20px 24px;border-radius:0 0 14px 14px;">
        <p>This is the <strong>Balanced</strong> strategy's current recommended squad for Gameweek {gw}. Make your transfers/team changes in the FPL app before the deadline above -- nothing here is submitted automatically.</p>
        {squad_html}
        <p style="margin-top:24px;"><a href="{SITE_URL}" style="background:#37003c;color:white;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;">Open full dashboard (all 5 strategies)</a></p>
        <p style="margin-top:20px;color:#9a9aa0;font-size:12px;">Automated reminder from PLFantasyBot. Not real-money advice.</p>
      </div>
    </div>
    """


def send_email(subject: str, html: str) -> None:
    api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["ALERT_FROM_EMAIL"]
    to_emails = [addr.strip() for addr in os.environ["ALERT_TO_EMAIL"].split(",") if addr.strip()]

    response = requests.post(
        RESEND_API,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": from_email, "to": to_emails, "subject": subject, "html": html},
        timeout=30,
    )
    response.raise_for_status()
    print(f"[ok] sent to {', '.join(to_emails)} -- {response.json().get('id')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="ignore the sent-log for the matched window")
    args = parser.parse_args()

    event = next_deadline()
    if event is None:
        print("No upcoming gameweek -- nothing to remind about.")
        return

    gw = event["gw"]
    deadline = datetime.fromisoformat(event["deadline"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    log = load_sent_log()
    already_sent = [] if args.force else log.get(str(gw), [])

    window = due_window(deadline, now, already_sent)
    if window is None:
        print(f"GW{gw} deadline {deadline.isoformat()} -- not due for any un-sent window right now.")
        return

    key, label = window
    print(f"GW{gw}: due for the {label}-before window. Sending...")

    squad = json.loads(SQUAD_PATH.read_text(encoding="utf-8")) if SQUAD_PATH.exists() else None
    html = build_email_html(gw, deadline, label, squad)
    send_email(f"PLFantasyBot: GW{gw} deadline in {label}", html)

    log.setdefault(str(gw), [])
    if key not in log[str(gw)]:
        log[str(gw)].append(key)
    save_sent_log(log)


if __name__ == "__main__":
    main()
