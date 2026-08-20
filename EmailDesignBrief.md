# PLFantasyBot deadline reminder — email design brief

Design an HTML email template for a Fantasy Premier League deadline reminder. This is **not** the web dashboard — it's a transactional email, sent via the Resend API, viewed in Gmail/Outlook/Apple Mail. Different constraints apply (see below).

## What this email is

A reminder sent automatically at **2 days, 1 day, 12 hours, and 1 hour** before an FPL gameweek deadline, showing the recommended squad so the recipient can go make that squad real in the FPL app before it locks. Two recipients, both already FPL players who understand the jargon.

## Content that must appear (all of it — this is a content spec, not just a mockup)

**Header**
- Gameweek number (e.g. "GW1")
- Which reminder this is: "2 days", "1 day", "12 hours", or "1 hour" until deadline
- The deadline itself, formatted as a real date/time (e.g. "Friday 21 August, 17:30 UTC")

**Body intro**
- One line stating this is the **Balanced** strategy's current squad, and that nothing is submitted automatically — the recipient still has to make the change themselves in the FPL app

**Starting XI** — a list/table, one row per player, each showing:
- Position (GKP/DEF/MID/FWD)
- Player name
- Captain / Vice-captain / Triple Captain badge, when applicable (C / VC / TC)
- Team and opponent (e.g. "Man City vs BOU (H)")
- Availability flag, when the player has one — a status code (injured/suspended/doubtful/unavailable) plus a chance-of-playing percentage when known (e.g. "[DOUBTFUL 75%]"), visually distinct/warning-colored since this is the kind of thing that changes a manager's mind

**Bench** — same row format as starting XI, separate section, in substitution-priority order

**Footer**
- A button/link to the full dashboard: `https://plfantasybot2026-27.vercel.app` (shows all 5 strategies, not just Balanced)
- Small disclaimer line: automated tool, not real-money advice

## Email-specific constraints (this is what makes it different from the dashboard)

- **No modern CSS.** No `backdrop-filter`, no CSS Grid/Flexbox reliability across clients, no external stylesheets, no `<style>` blocks trusted everywhere (Gmail strips many). Use **inline styles** and a **table-based layout** (`<table>`/`<tr>`/`<td>`) for anything structural — this is standard email-HTML practice, not a step backward.
- **Max width ~600px**, single column, mobile-friendly (most opens are on phones).
- **No custom fonts** beyond a system stack (`-apple-system, Segoe UI, Roboto, sans-serif` or similar web-safe fallback chain).
- **Images need absolute URLs** and should degrade gracefully if blocked (many clients block images by default) — don't put critical info *only* inside an image.
- **Dark mode**: some clients (Apple Mail, Outlook mobile) auto-invert colors. Either design something that survives inversion, or set explicit background colors defensively rather than relying on a transparent/white assumption.
- **No JavaScript** — email clients don't run it, ignore anything interactive from the dashboard's design language.

## Visual direction

Loosely match the dashboard's brand (FPL purple header `#37003c`/`#5e0067`, clean light body, the same restrained/product-designed feel) but simplified for email's constraints — think "a good transactional email from a well-designed product" rather than trying to replicate the dashboard's glass/blur aesthetic, which won't render in email clients anyway.

## Handoff format

Deliver as a single HTML file with inline styles, structured so the placeholder content (gameweek number, deadline, player rows, etc.) is easy to identify and swap for real template variables — same spirit as the dashboard handoff's demo-data-at-the-bottom approach, just email-safe HTML instead of a JS-rendered page.
