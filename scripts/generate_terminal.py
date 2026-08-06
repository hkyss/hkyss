#!/usr/bin/env python3
"""Render the GitHub contribution graph as an animated Vault-Tec terminal SVG.

A power-armour scanner sweeps the archive column by column; every logged day it
steps on flares and burns out. When the sweep finishes the grid reboots and the
loop starts over. Pure CSS animation, no scripts -- GitHub serves this through
camo as a plain <img>, so anything scripted would be inert.

Usage:
    python3 scripts/generate_terminal.py --user hkyss --out dist/fallout-terminal.svg
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# --- geometry -----------------------------------------------------------------

CELL = 11
GAP = 3
PITCH = CELL + GAP
ROWS = 7
PAD = 30

HEADER_Y = 32
RULE_TOP_Y = 46
MONTH_Y = 66
GRID_Y = 74

# --- palette (amber CRT) ------------------------------------------------------

BG = "#0a0906"
FRAME = "#ffb642"
DIM = "#a8781f"
LEVELS = ["#2b2008", "#6b4708", "#a06a0c", "#d0921a", "#ffb642"]
FLASH = "#fff6dd"

# --- animation timeline (percent of one loop) ---------------------------------

DURATION = "30s"
WALK_END = 82.0  # sweep runs 0% -> 82%
HOLD_END = 86.0  # scanner lingers, then fades out
RESET_AT = 90.0  # invisible jump back to the origin
RESTORE_FROM = 90.0  # grid reboots left-to-right
RESTORE_SPAN = 6.0
RESTORE_RISE = 3.0

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

FONT = "ui-monospace,'SF Mono',SFMono-Regular,Menlo,Consolas,'Courier New',monospace"
# Advance per glyph at font-size 11, tracking included. The reader's monospace font
# is unknown, so every run is pinned with textLength instead of trusting metrics.
ADVANCE = 8.2


def pct(value: float) -> str:
    """Percentages, trimmed -- this string is repeated a few thousand times."""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def run_width(text: str, size: float = 11) -> float:
    return len(text) * ADVANCE * (size / 11)


def label(text: str, x: float, y: float, fill: str, size: float = 11,
          glow: bool = False, inner: str | None = None) -> str:
    """A text run pinned to an exact width, so no font can shift the layout."""
    body = inner if inner is not None else html.escape(text)
    filt = ' filter="url(#softglow)"' if glow else ""
    return (
        f'<text x="{x:.1f}" y="{y}" fill="{fill}" font-family="{FONT}" font-size="{size}"'
        f' textLength="{run_width(text, size):.1f}" lengthAdjust="spacing"{filt}>{body}</text>'
    )


# --- data ---------------------------------------------------------------------

def fetch_graphql(user: str, token: str) -> dict | None:
    query = (
        "query($login:String!){user(login:$login){contributionsCollection"
        "{contributionCalendar{totalContributions weeks{contributionDays"
        "{date contributionCount weekday}}}}}}"
    )
    body = json.dumps({"query": query, "variables": {"login": user}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "fallout-terminal-generator",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"graphql fetch failed ({exc}); falling back to the public page", file=sys.stderr)
        return None

    if payload.get("errors") or not payload.get("data", {}).get("user"):
        print(f"graphql returned no calendar: {payload.get('errors')}", file=sys.stderr)
        return None

    calendar = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = [
        {"date": day["date"], "count": day["contributionCount"], "row": day["weekday"], "col": col}
        for col, week in enumerate(calendar["weeks"])
        for day in week["contributionDays"]
    ]
    counts = sorted(d["count"] for d in days if d["count"] > 0)
    for day in days:
        day["level"] = level_for(day["count"], counts)
    return {"days": days, "total": calendar["totalContributions"]}


def level_for(count: int, sorted_counts: list[int]) -> int:
    """GitHub buckets by quartile of the year's non-zero days; mirror that."""
    if count <= 0 or not sorted_counts:
        return 0
    quartiles = [sorted_counts[int(len(sorted_counts) * q)] for q in (0.25, 0.5, 0.75)]
    if count < quartiles[0]:
        return 1
    if count < quartiles[1]:
        return 2
    if count < quartiles[2]:
        return 3
    return 4


def fetch_scrape(user: str) -> dict:
    url = f"https://github.com/users/{user}/contributions"
    req = urllib.request.Request(url, headers={"User-Agent": "fallout-terminal-generator"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        markup = resp.read().decode("utf-8", "replace")

    days = []
    cell = re.compile(
        r'<td[^>]*?data-date="(\d{4}-\d{2}-\d{2})"[^>]*?'
        r'id="contribution-day-component-(\d+)-(\d+)"[^>]*?data-level="(\d+)"'
    )
    for iso, row, col, level in cell.findall(markup):
        days.append({"date": iso, "row": int(row), "col": int(col),
                     "level": int(level), "count": None})
    if not days:
        raise SystemExit(f"could not parse any contribution cells from {url}")

    total = 0
    headline = re.search(r"([\d,]+)\s*\n?\s*contributions?\s*\n?\s*in the last year", markup)
    if headline:
        total = int(headline.group(1).replace(",", ""))
    return {"days": days, "total": total}


def load_calendar(user: str, token: str | None) -> dict:
    if token:
        data = fetch_graphql(user, token)
        if data:
            return data
    return fetch_scrape(user)


# --- svg ----------------------------------------------------------------------

def build_svg(user: str, data: dict) -> str:
    days = data["days"]
    cols = max(d["col"] for d in days) + 1
    grid = {(d["row"], d["col"]): d for d in days}

    grid_w = cols * PITCH - GAP
    grid_h = ROWS * PITCH - GAP
    width = grid_w + PAD * 2
    grid_x = PAD
    rule_bottom_y = GRID_Y + grid_h + 17
    footer_y = rule_bottom_y + 18
    height = footer_y + 16

    def cell_x(col: int) -> float:
        return grid_x + col * PITCH

    def cell_y(row: int) -> float:
        return GRID_Y + row * PITCH

    # Boustrophedon sweep: down one week, up the next, so the scanner never jumps.
    path = []
    for col in range(cols):
        rows = range(ROWS) if col % 2 == 0 else range(ROWS - 1, -1, -1)
        path.extend((row, col) for row in rows)
    steps = len(path)

    eat_at = {position: i / (steps - 1) * WALK_END for i, position in enumerate(path)}

    css: list[str] = []
    rects: list[str] = []

    for (row, col), day in sorted(grid.items()):
        x, y = cell_x(col), cell_y(row)
        level = day["level"]
        eat = eat_at[(row, col)]
        name = f"e{row}_{col}"

        if level == 0:
            # Nothing to burn, but the sweep should still register -- a dim ping
            # keeps the scan line legible across empty stretches of the year.
            css.append(
                f"@keyframes {name}{{"
                f"0%,{pct(eat)}%{{fill:{LEVELS[0]}}}"
                f"{pct(eat + 0.28)}%{{fill:{LEVELS[1]}}}"
                f"{pct(eat + 2.0)}%,100%{{fill:{LEVELS[0]}}}}}"
            )
            rects.append(
                f'<rect class="c" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.4"'
                f' fill="{LEVELS[0]}" style="animation-name:{name}"/>'
            )
            continue

        colour = LEVELS[level]
        flash = eat + 0.28
        burn = eat + 3.0
        back = RESTORE_FROM + (col / max(cols - 1, 1)) * RESTORE_SPAN
        css.append(
            f"@keyframes {name}{{"
            f"0%,{pct(eat)}%{{fill:{colour};transform:scale(1)}}"
            f"{pct(flash)}%{{fill:{FLASH};transform:scale(1.55)}}"
            f"{pct(burn)}%{{fill:{LEVELS[0]};transform:scale(1)}}"
            f"{pct(back)}%{{fill:{LEVELS[0]}}}"
            f"{pct(back + RESTORE_RISE)}%,100%{{fill:{colour}}}}}"
        )
        # transform-origin in viewBox units rather than transform-box:fill-box,
        # which only landed in Safari 14.
        rects.append(
            f'<rect class="c" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.4"'
            f' fill="{colour}" style="animation-name:{name};'
            f'transform-origin:{x + CELL / 2}px {y + CELL / 2}px"/>'
        )

    # Scanner track.
    walk = []
    for i, (row, col) in enumerate(path):
        at = i / (steps - 1) * WALK_END
        bob = -1.0 if i % 2 else 0.6  # a slight gait, smoothed by linear interpolation
        walk.append(
            f"{pct(at)}%{{transform:translate({cell_x(col) + CELL / 2:.1f}px,"
            f"{cell_y(row) + CELL / 2 + bob:.1f}px)}}"
        )
    home_x = cell_x(path[0][1]) + CELL / 2
    home_y = cell_y(path[0][0]) + CELL / 2
    last_x = cell_x(path[-1][1]) + CELL / 2
    last_y = cell_y(path[-1][0]) + CELL / 2
    walk.append(f"{pct(HOLD_END)}%{{transform:translate({last_x:.1f}px,{last_y:.1f}px)}}")
    walk.append(f"{pct(RESET_AT)}%,100%{{transform:translate({home_x:.1f}px,{home_y:.1f}px)}}")
    css.append("@keyframes walk{" + "".join(walk) + "}")

    css.append(
        "@keyframes hero-fade{"
        f"0%,{pct(WALK_END + 1)}%{{opacity:1}}"
        f"{pct(HOLD_END)}%{{opacity:0}}"
        f"{pct(RESET_AT + 4)}%{{opacity:0}}"
        "100%{opacity:1}}"
    )

    css.insert(0, (
        f".c{{animation-duration:{DURATION};animation-timing-function:linear;"
        "animation-iteration-count:infinite}"
        f"#hero{{animation:walk {DURATION} linear infinite,hero-fade {DURATION} linear infinite}}"
        "#beam{animation:beam 7s linear infinite}"
        "#flicker{animation:flicker 3.1s steps(1,end) infinite}"
        "#caret{animation:caret 1.06s steps(1,end) infinite}"
        "#pulse{animation:pulse 1.4s ease-in-out infinite}"
    ))

    css.append("@keyframes beam{0%{transform:translateY(-30px)}"
               f"100%{{transform:translateY({grid_h + 30}px)}}}}")
    css.append("@keyframes flicker{0%{opacity:.02}18%{opacity:.05}23%{opacity:.015}"
               "52%{opacity:.045}58%{opacity:.02}81%{opacity:.06}86%{opacity:.02}100%{opacity:.03}}")
    css.append("@keyframes caret{0%{opacity:1}50%{opacity:0}100%{opacity:1}}")
    css.append("@keyframes pulse{0%,100%{opacity:.35}50%{opacity:1}}")

    # Month ruler.
    months = []
    last_month = None
    last_col = -9
    for col in range(cols):
        day = next((grid[(r, col)] for r in range(ROWS) if (r, col) in grid), None)
        if not day:
            continue
        month = int(day["date"][5:7])
        name = MONTHS[month - 1]
        x = cell_x(col)
        if month != last_month and col - last_col >= 3 and x + run_width(name, 9) <= width - PAD:
            months.append(label(name, x, MONTH_Y, DIM, size=9))
            last_month, last_col = month, col

    dates = sorted(d["date"] for d in days)
    total = f"{data['total']:,}".replace(",", " ") if data["total"] else "--"
    logged = sum(1 for d in days if d["level"] > 0)
    right_edge = width - PAD

    head_left = "ROBCO INDUSTRIES (TM) TERMLINK PROTOCOL"
    head_right = f"USER: {user.upper()}"
    while PAD + run_width(head_left) + 24 > right_edge - run_width(head_right):
        head_right = head_right[:-1]  # absurdly long handle: clip it rather than collide

    foot_left = f"> ARCHIVE SCAN :: {total} ENTRIES / {logged} ACTIVE DAYS "
    foot_right = f"{dates[0]} / {dates[-1]}"
    foot_left_svg = label(
        foot_left + "_", PAD, footer_y, FRAME, glow=True,
        inner=f'<tspan>{html.escape(foot_left)}</tspan><tspan id="caret">_</tspan>',
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Contribution archive of {html.escape(user)} rendered as a Vault-Tec terminal scan">
<title>{html.escape(user)} — Vault-Tec archive scan</title>
<defs>
<style>{"".join(css)}</style>
<filter id="glow" x="-120%" y="-120%" width="340%" height="340%">
<feGaussianBlur stdDeviation="1.7" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>
<filter id="softglow" x="-60%" y="-60%" width="220%" height="220%">
<feGaussianBlur stdDeviation="0.9" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>
<radialGradient id="lamp"><stop offset="0" stop-color="{FRAME}" stop-opacity=".38"/><stop offset="1" stop-color="{FRAME}" stop-opacity="0"/></radialGradient>
<linearGradient id="beamGrad" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="{FRAME}" stop-opacity="0"/><stop offset=".65" stop-color="{FRAME}" stop-opacity=".55"/><stop offset="1" stop-color="{FRAME}" stop-opacity="0"/>
</linearGradient>
<radialGradient id="vignette" cx=".5" cy=".5" r=".78">
<stop offset=".55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".55"/>
</radialGradient>
<pattern id="scan" width="4" height="3" patternUnits="userSpaceOnUse">
<rect width="4" height="1" fill="{FRAME}" opacity=".07"/>
</pattern>
<clipPath id="screen"><rect x="3" y="3" width="{width - 6}" height="{height - 6}" rx="10"/></clipPath>
<clipPath id="gridClip"><rect x="{grid_x - 4}" y="{GRID_Y - 4}" width="{grid_w + 8}" height="{grid_h + 8}"/></clipPath>
</defs>

<rect width="{width}" height="{height}" rx="12" fill="{BG}"/>
<g clip-path="url(#screen)">

{label(head_left, PAD, HEADER_Y, FRAME, glow=True)}
{label(head_right, right_edge - run_width(head_right), HEADER_Y, DIM)}
<rect x="{PAD}" y="{RULE_TOP_Y}" width="{grid_w}" height="1" fill="{FRAME}" opacity=".35"/>

{"".join(months)}

<g clip-path="url(#gridClip)">
{"".join(rects)}
<rect id="beam" x="{grid_x - 4}" y="{GRID_Y - 4}" width="{grid_w + 8}" height="26" fill="url(#beamGrad)" opacity=".16"/>
</g>

<g id="hero" filter="url(#glow)">
<circle r="15" fill="url(#lamp)"/>
<path d="M-6.5 4.5 L-6.5 -1.5 A6.5 6.5 0 0 1 6.5 -1.5 L6.5 4.5 Z" fill="#120d04" stroke="{FRAME}" stroke-width="1.1" stroke-linejoin="round"/>
<path d="M-7.4 4.6 L7.4 4.6 L6.2 7.8 L-6.2 7.8 Z" fill="#120d04" stroke="{FRAME}" stroke-width="1.1" stroke-linejoin="round"/>
<rect x="-5.4" y="-1.4" width="10.8" height="4.6" rx="1.3" fill="#080502" stroke="{FRAME}" stroke-width=".8"/>
<circle cx="-2.6" cy="1" r="1.35" fill="{FLASH}"/>
<circle cx="2.6" cy="1" r="1.35" fill="{FLASH}"/>
<path d="M5.6 -4.6 L8.6 -9.4" stroke="{FRAME}" stroke-width="1" fill="none"/>
<circle id="pulse" cx="8.9" cy="-10" r="1.4" fill="{FRAME}"/>
</g>

<rect x="{PAD}" y="{rule_bottom_y}" width="{grid_w}" height="1" fill="{FRAME}" opacity=".35"/>
{foot_left_svg}
{label(foot_right, right_edge - run_width(foot_right), footer_y, DIM)}

<rect width="{width}" height="{height}" fill="url(#scan)"/>
<rect width="{width}" height="{height}" fill="url(#vignette)"/>
<rect id="flicker" width="{width}" height="{height}" fill="{FRAME}" opacity=".03"/>
</g>
<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="11" fill="none" stroke="{FRAME}" stroke-width="1" opacity=".45"/>
</svg>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--out", default="dist/fallout-terminal.svg")
    args = parser.parse_args()

    data = load_calendar(args.user, os.environ.get("GITHUB_TOKEN"))
    svg = build_svg(args.user, data)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"wrote {out} ({len(svg) / 1024:.0f} KB, {data['total']} contributions)")


if __name__ == "__main__":
    main()
