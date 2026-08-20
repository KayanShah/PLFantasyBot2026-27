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

HTML template is the design handoff (~/Downloads/plfantasybot-deadline-
reminder-email.html) -- table-based layout, all CSS inline, no backdrop-
filter/grid/JS, since email clients don't render any of what the web
dashboard uses. Only the player rows and three header variables are
generated; everything else is that file's markup unchanged.

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

CAPTAIN_BADGES = {
    "tc": '<span style="display:inline-block; padding:2px 6px; margin-left:4px; border-radius:999px; background-color:#5e0067; border:1px solid #5e0067; color:#ffffff; font-size:9px; line-height:12px; font-weight:800;">TC</span>',
    "c": '<span style="display:inline-block; padding:2px 6px; margin-left:4px; border-radius:999px; background-color:#37003c; border:1px solid #37003c; color:#ffffff; font-size:9px; line-height:12px; font-weight:800;">C</span>',
    "c_star": '<span style="display:inline-block; padding:2px 6px; margin-left:4px; border-radius:999px; background-color:#37003c; border:1px solid #37003c; color:#ffffff; font-size:9px; line-height:12px; font-weight:800;">C*</span>',
    "vc": '<span style="display:inline-block; padding:2px 6px; margin-left:4px; border-radius:999px; background-color:#efe8f2; border:1px solid #d9c7df; color:#5e0067; font-size:9px; line-height:12px; font-weight:800;">VC</span>',
}
STATUS_LABELS = {"i": "INJURED", "s": "SUSPENDED", "d": "DOUBTFUL", "u": "UNAVAILABLE", "n": "NOT IN SQUAD"}
AMBER = ("#fff0c7", "#e6c66d", "#805e09")
RED = ("#fde2e2", "#e9aaaa", "#9a2f2f")

EMAIL_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="x-apple-disable-message-reformatting">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <meta name="format-detection" content="telephone=no,address=no,email=no,date=no,url=no">
  <title>PLFantasyBot Deadline Reminder</title>
</head>
<body style="margin:0; padding:0; background-color:#eef0f4; color:#1d1d1f; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
  <div style="display:none; max-height:0; overflow:hidden; opacity:0; color:transparent; mso-hide:all;">
    __GW_LABEL__ locks in __REMINDER_LABEL__. Review the Balanced squad before the deadline.
  </div>

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#eef0f4" style="width:100%; background-color:#eef0f4; margin:0; padding:0;">
    <tr>
      <td align="center" style="padding:26px 12px 36px 12px;">

        <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" bgcolor="#f7f7f8" style="width:100%; max-width:600px; background-color:#f7f7f8; border:1px solid #d9dce2; border-radius:24px; box-shadow:0 18px 48px rgba(24,26,34,0.10); overflow:hidden;">

          <tr>
            <td bgcolor="#37003c" style="background-color:#37003c; padding:0; border-radius:24px 24px 0 0;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;">
                <tr>
                  <td style="padding:24px 24px 20px 24px;">
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;">
                      <tr>
                        <td valign="top" style="padding:0;">
                          <div style="font-size:12px; line-height:16px; font-weight:700; letter-spacing:1.2px; text-transform:uppercase; color:#d8c9dc;">PLFantasyBot</div>
                          <div style="font-size:34px; line-height:38px; font-weight:760; letter-spacing:-1.2px; color:#ffffff; margin-top:4px;">__GW_LABEL__</div>
                        </td>
                        <td align="right" valign="top" style="padding:0;">
                          <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="right">
                            <tr>
                              <td bgcolor="#5e0067" style="background-color:#5e0067; border:1px solid #7c2d83; border-radius:999px; padding:8px 11px; color:#ffffff; font-size:12px; line-height:14px; font-weight:700; white-space:nowrap; box-shadow:inset 0 1px 0 #7e3b86;">
                                __REMINDER_LABEL__ to go
                              </td>
                            </tr>
                          </table>
                        </td>
                      </tr>
                    </table>

                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%; margin-top:18px;">
                      <tr>
                        <td bgcolor="#4b1051" style="background-color:#4b1051; border:1px solid #69306e; border-radius:14px; padding:13px 14px; box-shadow:inset 0 1px 0 #6c3471;">
                          <div style="font-size:10px; line-height:14px; color:#d7c8da; letter-spacing:.8px; text-transform:uppercase; font-weight:700;">Deadline</div>
                          <div style="font-size:16px; line-height:22px; color:#ffffff; font-weight:700; margin-top:2px;">__DEADLINE_FORMATTED__</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:22px 22px 0 22px; background-color:#f7f7f8;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%; background-color:#ffffff; border:1px solid #e2e4e9; border-radius:18px; box-shadow:0 8px 20px rgba(27,31,42,.06);">
                <tr>
                  <td style="padding:18px 18px 17px 18px;">
                    <div style="font-size:15px; line-height:22px; color:#1d1d1f; font-weight:700;">Balanced strategy &middot; current squad</div>
                    <div style="font-size:13px; line-height:20px; color:#66676c; margin-top:4px;">
                      This is the Balanced strategy's current recommendation. Nothing is submitted automatically, so you still need to make the changes yourself in the official FPL app before the deadline.
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:16px 22px 0 22px; background-color:#f7f7f8;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#dff1e5" style="width:100%; background-color:#dff1e5; border:1px solid #c8e3d1; border-radius:20px; box-shadow:0 10px 24px rgba(39,95,61,.08); overflow:hidden;">
                <tr>
                  <td style="padding:18px 18px 8px 18px;">
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;">
                      <tr>
                        <td valign="middle" style="font-size:15px; line-height:20px; font-weight:760; color:#183f2b;">Starting XI</td>
                        <td align="right" valign="middle" style="font-size:10px; line-height:14px; color:#4f7e62; font-weight:700; letter-spacing:.7px; text-transform:uppercase;">Balanced</td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:0 18px 8px 18px;">
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%; border-top:1px solid #b9d9c4;">
                      <tr><td height="1" style="font-size:1px; line-height:1px;">&nbsp;</td></tr>
                    </table>
                  </td>
                </tr>
__STARTING_XI_ROWS__
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:16px 22px 0 22px; background-color:#f7f7f8;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%; background-color:#ffffff; border:1px solid #e2e4e9; border-radius:18px; box-shadow:0 8px 20px rgba(27,31,42,.06);">
                <tr>
                  <td style="padding:17px 16px 10px 16px;">
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td style="font-size:15px; line-height:20px; font-weight:760; color:#1d1d1f;">Bench</td><td align="right" style="font-size:10px; color:#85868b; font-weight:700; letter-spacing:.5px; text-transform:uppercase;">Sub priority</td></tr></table>
                  </td>
                </tr>
__BENCH_ROWS__
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:18px 22px 0 22px; background-color:#f7f7f8;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f1edf4" style="width:100%; background-color:#f1edf4; border:1px solid #e0d7e4; border-radius:18px; box-shadow:inset 0 1px 0 #ffffff;">
                <tr>
                  <td align="center" style="padding:20px 18px 18px 18px;">
                    <div style="font-size:14px; line-height:20px; font-weight:760; color:#2a1d2c;">Want to compare all five strategies?</div>
                    <div style="font-size:11px; line-height:17px; color:#746b77; margin-top:4px;">Open the full dashboard before you lock your team.</div>

                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin-top:14px;">
                      <tr>
                        <td bgcolor="#37003c" style="background-color:#37003c; border-radius:12px; box-shadow:0 7px 14px rgba(55,0,60,.18), inset 0 1px 0 #6a276f;">
                          <a href="__SITE_URL__" target="_blank" style="display:inline-block; padding:13px 20px; color:#ffffff; text-decoration:none; font-size:13px; line-height:16px; font-weight:760; border-radius:12px;">Open PLFantasyBot dashboard</a>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:18px 24px 24px 24px; background-color:#f7f7f8; border-radius:0 0 24px 24px;">
              <div style="font-size:10px; line-height:16px; color:#8a8c92; text-align:center;">
                PLFantasyBot is an automated analytical tool. It is not real-money betting or financial advice.
              </div>
              <div style="font-size:9px; line-height:14px; color:#a3a5aa; text-align:center; margin-top:7px;">
                This reminder was generated automatically for __GW_LABEL__ &middot; __REMINDER_LABEL__ before deadline.
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


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


def badge_html(p: dict) -> str:
    if p.get("is_triple_captain"):
        return CAPTAIN_BADGES["tc"]
    if p.get("is_captain"):
        return CAPTAIN_BADGES["c"]
    if p.get("is_effective_captain"):
        return CAPTAIN_BADGES["c_star"]
    if p.get("is_vice_captain"):
        return CAPTAIN_BADGES["vc"]
    return ""


def availability_html(p: dict) -> str:
    status = p.get("status")
    if not status:
        return ""
    label = STATUS_LABELS.get(status, status.upper())
    pct = p.get("chance_of_playing")
    if pct is not None:
        label = f"{label} {pct}%"
    bg, border, color = AMBER if status == "d" else RED
    return (
        f'<span style="display:inline-block; padding:5px 7px; border-radius:8px; '
        f'background-color:{bg}; border:1px solid {border}; color:{color}; '
        f'font-size:9px; line-height:12px; font-weight:850; white-space:nowrap;">{label}</span>'
    )


def starting_xi_row(p: dict) -> str:
    flagged = bool(p.get("status"))
    bg, border, shadow = ("#fff9ed", "#efd9a8", "rgba(103,77,25,.05)") if flagged else ("#f9fcfa", "#cfe3d6", "rgba(35,80,52,.05)")
    right_cell = (
        f'<td width="118" align="right" valign="middle" style="padding:10px 12px 10px 6px;">{availability_html(p)}</td>'
        if flagged else
        '<td width="70" align="right" valign="middle" style="padding:10px 12px 10px 6px;">&nbsp;</td>'
    )
    return (
        f'<tr><td style="padding:0 12px 8px 12px;">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="{bg}" '
        f'style="width:100%; background-color:{bg}; border:1px solid {border}; border-radius:14px; '
        f'box-shadow:inset 0 1px 0 #ffffff,0 4px 10px {shadow};"><tr>'
        f'<td width="48" valign="middle" style="padding:12px 8px 12px 12px; font-size:10px; line-height:14px; font-weight:800; color:#6a6b70; letter-spacing:.5px;">{p["position"]}</td>'
        f'<td valign="middle" style="padding:10px 6px;">'
        f'<div style="font-size:13px; line-height:18px; font-weight:750; color:#1f2023;">{p["name"]}{badge_html(p)}</div>'
        f'<div style="font-size:11px; line-height:16px; color:#6b6d72; margin-top:1px;">{p["team"]} vs {p["opponent"]}</div>'
        f'</td>{right_cell}</tr></table></td></tr>'
    )


def bench_row(p: dict, sub_priority: int) -> str:
    flagged = bool(p.get("status"))
    bg, border = ("#fff3f3", "#edc9c9") if flagged else ("#fafafb", "#e5e6ea")
    right_cell = (
        f'<td width="118" align="right" valign="middle" style="padding:9px 12px 9px 6px;">{availability_html(p)}</td>'
        if flagged else
        f'<td width="52" align="right" valign="middle" style="padding:9px 12px 9px 6px; color:#9a9ca1; font-size:10px; font-weight:800;">{sub_priority}</td>'
    )
    return (
        f'<tr><td style="padding:0 12px 8px 12px;">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="{bg}" '
        f'style="width:100%; background-color:{bg}; border:1px solid {border}; border-radius:12px;"><tr>'
        f'<td width="48" valign="middle" style="padding:11px 8px 11px 12px; font-size:10px; font-weight:800; color:#73757a;">{p["position"]}</td>'
        f'<td valign="middle" style="padding:9px 6px;">'
        f'<div style="font-size:13px; line-height:18px; font-weight:730; color:#222326;">{p["name"]}{badge_html(p)}</div>'
        f'<div style="font-size:11px; line-height:16px; color:#72747a;">{p["team"]} vs {p["opponent"]}</div>'
        f'</td>{right_cell}</tr></table></td></tr>'
    )


def build_email_html(gw: int, deadline: datetime, window_label: str, squad: dict | None) -> str:
    gw_label = f"GW{gw}"
    deadline_str = format_deadline(deadline)

    if squad is None:
        xi_html = '<tr><td style="padding:12px 18px;color:#6a6b70;">No squad data available yet.</td></tr>'
        bench_html = '<tr><td style="padding:12px 18px;color:#6a6b70;">No squad data available yet.</td></tr>'
    else:
        gw_data = squad["gameweeks"][0]
        xi_sorted = sorted(gw_data["starting_xi"], key=lambda p: POSITION_ORDER.index(p["position"]))
        xi_html = "\n".join(starting_xi_row(p) for p in xi_sorted)
        bench_html = "\n".join(bench_row(p, i + 1) for i, p in enumerate(gw_data["bench"]))

    html = EMAIL_TEMPLATE
    html = html.replace("__GW_LABEL__", gw_label)
    html = html.replace("__REMINDER_LABEL__", window_label)
    html = html.replace("__DEADLINE_FORMATTED__", deadline_str)
    html = html.replace("__SITE_URL__", SITE_URL)
    html = html.replace("__STARTING_XI_ROWS__", xi_html)
    html = html.replace("__BENCH_ROWS__", bench_html)
    return html


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
