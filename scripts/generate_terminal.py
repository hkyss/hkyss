#!/usr/bin/env python3
"""Render a GitHub profile as a single Vault-Tec terminal readout."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAD = 30
CELL = 11
GAP = 3
PITCH = CELL + GAP
ROWS = 7

TAB_Y = 30
TAB_LINE_Y = 38
GRID_Y = 58
NAME_Y = 108
ROLE_Y = 136
SEC1_Y = 166
SEC2_Y = 326
DIAG_HEAD_Y = 348
ROW_Y = 374
ROW_STEP = 21
HEIGHT = 536

BG = "#0a0906"
FRAME = "#ffb642"
DIM = "#a8781f"
PANEL = "#3a2a0b"
LEVELS = ["#2b2008", "#6b4708", "#a06a0c", "#d0921a", "#ffb642"]
FLASH = "#fff6dd"
SHADE = "#120d04"

TAB = "STAT"

DURATION = "30s"
WALK_END = 82.0
HOLD_END = 86.0
RESET_AT = 90.0
RESTORE_FROM = 90.0
RESTORE_SPAN = 6.0
RESTORE_RISE = 3.0

FONT = "ui-monospace,'SF Mono',SFMono-Regular,Menlo,Consolas,'Courier New',monospace"
ADVANCE = 8.2

BASE_CSS = (
    "#flicker{animation:flicker 3.1s steps(1,end) infinite}"
    ".caret{animation:caret 1.06s steps(1,end) infinite}"
    f".bar{{animation:fill {DURATION} ease-out infinite backwards}}"
        "@keyframes flicker{0%{opacity:.02}18%{opacity:.05}23%{opacity:.015}52%{opacity:.045}"
    "58%{opacity:.02}81%{opacity:.06}86%{opacity:.02}100%{opacity:.03}}"
    "@keyframes caret{0%{opacity:1}50%{opacity:0}100%{opacity:1}}"
    "@keyframes fill{0%{transform:scaleX(0)}6%,80%{transform:scaleX(1)}87%,100%{transform:scaleX(0)}}"
)


def pct(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def run_width(text: str, size: float = 11) -> float:
    return len(text) * ADVANCE * (size / 11)


def label(text: str, x: float, y: float, fill: str, size: float = 11,
          glow: bool = False, inner: str | None = None, weight: str = "") -> str:
    """A text run pinned to an exact width, so no font substitution can shift the layout."""
    body = inner if inner is not None else html.escape(text)
    filt = ' filter="url(#softglow)"' if glow else ""
    bold = f' font-weight="{weight}"' if weight else ""
    return (
        f'<text x="{x:.1f}" y="{y}" fill="{fill}" font-family="{FONT}" font-size="{size}"{bold}'
        f' textLength="{run_width(text, size):.1f}" lengthAdjust="spacing"{filt}>{body}</text>'
    )


def right(text: str, edge: float, y: float, fill: str, size: float = 11, **kw) -> str:
    return label(text, edge - run_width(text, size), y, fill, size, **kw)


def rule(width: float, y: float) -> str:
    return (f'<rect x="{PAD}" y="{y}" width="{width - PAD * 2}" height="1"'
            f' fill="{FRAME}" opacity=".35"/>')


def heading(width: float, y: float, left: str, tag: str = "") -> str:
    tail = right(tag, width - PAD, y, DIM) if tag else ""
    return label(left, PAD, y, FRAME, glow=True) + tail


def tab_bar(width: float) -> str:
    w = run_width(TAB, 15)
    slot = (width - PAD * 2) / 5
    x = PAD + (slot - w) / 2
    x0, x1 = x - 8, x + w + 8
    top = TAB_Y - 15
    return (
        label(TAB, x, TAB_Y, FRAME, size=15, glow=True, weight="700")
        + f'<path d="M{PAD} {TAB_LINE_Y + 6} V{TAB_LINE_Y} H{x0:.1f} V{top}'
        f' M{x1:.1f} {top} V{TAB_LINE_Y} H{width - PAD} V{TAB_LINE_Y + 6}"'
        f' fill="none" stroke="{FRAME}" stroke-width="1.6" filter="url(#softglow)"/>'
    )


def status_bar(width: float, profile: dict | None, cal: dict) -> str:
    current, longest = streaks(cal["days"])
    active = sum(1 for d in cal["days"] if d["level"] > 0)
    top, h = HEIGHT - 38, 22
    base = top + 15
    gap = 6
    side = 170
    mid_x = PAD + side + gap
    mid_w = width - PAD * 2 - side * 2 - gap * 2
    right_x = mid_x + mid_w + gap

    level, progress = "--", 0.0
    if profile:
        born = datetime.fromisoformat(profile["createdAt"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days = (now - born).days
        level, progress = str(days // 365 + 1), (days % 365) / 365

    def box(x: float, w: float) -> str:
        return f'<rect x="{x:.1f}" y="{top}" width="{w:.1f}" height="{h}" fill="{PANEL}" opacity=".85"/>'

    lvl_text = f"LEVEL {level}"
    bar_x = mid_x + 14 + run_width(lvl_text, 12) + 14
    bar_w = mid_x + mid_w - 14 - bar_x
    return "".join([
        box(PAD, side), box(mid_x, mid_w), box(right_x, side),
        label("HP", PAD + 10, base, FRAME, size=12, weight="700"),
        right(f"{current}/{longest}", PAD + side - 10, base, FRAME, size=12),
        label(lvl_text, mid_x + 14, base, FRAME, size=12, weight="700"),
        f'<rect x="{bar_x:.1f}" y="{top + 6}" width="{bar_w:.1f}" height="10" fill="none"'
        f' stroke="{FRAME}" stroke-width="1.2"/>',
        f'<rect x="{bar_x + 2:.1f}" y="{top + 8}" width="{max(bar_w - 4, 0) * progress:.1f}"'
        f' height="6" fill="{FRAME}"/>',
        label("AP", right_x + 10, base, FRAME, size=12, weight="700"),
        right(f"{active}/{len(cal['days'])}", right_x + side - 10, base, FRAME, size=12),
    ])


# --- data ---------------------------------------------------------------------

PROFILE_QUERY = """
query($login:String!){user(login:$login){
  createdAt
  followers{totalCount}
  contributionsCollection{
    totalCommitContributions totalPullRequestContributions totalIssueContributions
    contributionCalendar{totalContributions weeks{contributionDays{date contributionCount weekday}}}
  }
  repositories(first:100,ownerAffiliations:OWNER,isFork:false){
    totalCount
    nodes{stargazerCount languages(first:8,orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}}}
  }
}}
"""


def graphql(user: str, token: str) -> dict | None:
    body = json.dumps({"query": PROFILE_QUERY, "variables": {"login": user}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "vault-tec-terminal"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"graphql request failed ({exc})", file=sys.stderr)
        return None
    if payload.get("errors") or not payload.get("data", {}).get("user"):
        print(f"graphql returned no profile: {payload.get('errors')}", file=sys.stderr)
        return None
    return payload["data"]["user"]


def level_for(count: int, sorted_counts: list[int]) -> int:
    if count <= 0 or not sorted_counts:
        return 0
    q = [sorted_counts[int(len(sorted_counts) * f)] for f in (0.25, 0.5, 0.75)]
    return 1 if count < q[0] else 2 if count < q[1] else 3 if count < q[2] else 4


def scrape_calendar(user: str) -> dict:
    url = f"https://github.com/users/{user}/contributions"
    req = urllib.request.Request(url, headers={"User-Agent": "vault-tec-terminal"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        markup = resp.read().decode("utf-8", "replace")

    pattern = re.compile(
        r'<td[^>]*?data-date="(\d{4}-\d{2}-\d{2})"[^>]*?'
        r'id="contribution-day-component-(\d+)-(\d+)"[^>]*?data-level="(\d+)"'
    )
    days = [{"date": d, "row": int(r), "col": int(c), "level": int(l)}
            for d, r, c, l in pattern.findall(markup)]
    if not days:
        raise SystemExit(f"could not parse any contribution cells from {url}")

    headline = re.search(r"([\d,]+)\s*\n?\s*contributions?\s*\n?\s*in the last year", markup)
    return {"days": days, "total": int(headline.group(1).replace(",", "")) if headline else 0}


def calendar_from_profile(profile: dict) -> dict:
    cal = profile["contributionsCollection"]["contributionCalendar"]
    days = [{"date": d["date"], "count": d["contributionCount"], "row": d["weekday"], "col": col}
            for col, week in enumerate(cal["weeks"]) for d in week["contributionDays"]]
    counts = sorted(d["count"] for d in days if d["count"] > 0)
    for d in days:
        d["level"] = level_for(d["count"], counts)
    return {"days": days, "total": cal["totalContributions"]}


def streaks(days: list[dict]) -> tuple[int, int]:
    """Current streak ignores a still-empty today, matching how GitHub itself reads it."""
    ordered = sorted(days, key=lambda d: d["date"])
    longest = run = 0
    for day in ordered:
        run = run + 1 if day["level"] > 0 else 0
        longest = max(longest, run)

    current = 0
    for day in reversed(ordered):
        if day["level"] > 0:
            current += 1
        elif current or day["date"] != ordered[-1]["date"]:
            break
    return current, longest


EXCLUDED_LANGUAGES = {"CSS", "HTML", "Blade", "Twig"}


def top_languages(profile: dict, limit: int = 6) -> list[tuple[str, float]]:
    totals: dict[str, int] = {}
    for repo in profile["repositories"]["nodes"]:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name in EXCLUDED_LANGUAGES:
                continue
            totals[name] = totals.get(name, 0) + edge["size"]
    grand = sum(totals.values())
    if not grand:
        return []
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:limit]
    return [(name, size / grand * 100) for name, size in ranked]


# --- sections -----------------------------------------------------------------

TYPE_CYCLE = 11.0
TYPE_START = 0.8
TYPE_STEP = 0.55
TYPE_HOLD = 4.5
ERASE_STEP = 0.3


def typing_name(name: str, size: float = 44) -> tuple[str, str]:
    adv = ADVANCE * size / 11
    n = len(name)
    full = TYPE_START + TYPE_STEP * (n - 1)
    erase_from = full + TYPE_HOLD

    def at(t: float) -> str:
        return pct(t / TYPE_CYCLE * 100)

    parts, css, events = [], [], [(0.0, 0)]
    for i, ch in enumerate(name):
        shown = TYPE_START + TYPE_STEP * i
        gone = erase_from + ERASE_STEP * (n - 1 - i)
        events += [(shown, i + 1), (gone, i)]
        css.append(f"@keyframes t{i}{{0%{{opacity:0}}{at(shown)}%{{opacity:1}}{at(gone)}%,100%{{opacity:0}}}}"
                   f".t{i}{{opacity:0;animation:t{i} {TYPE_CYCLE}s steps(1,end) infinite}}")
        parts.append(f'<g class="t{i}">' + label(ch, PAD + adv * i, NAME_Y, FRAME, size=size,
                                                 glow=True, weight="700") + "</g>")

    moves = "".join(f"{at(t)}%{{transform:translateX({adv * k:.1f}px)}}" for t, k in sorted(events))
    css.append(f"@keyframes typecaret{{{moves}100%{{transform:translateX(0)}}}}"
               f"#typecaret{{animation:typecaret {TYPE_CYCLE}s steps(1,end) infinite}}")
    parts.append('<g id="typecaret">' + label("_", PAD, NAME_Y, FRAME, size=size, glow=True,
                                              weight="700", inner='<tspan class="caret">_</tspan>') + "</g>")
    return "".join(parts), "".join(css)


def identity(user: str) -> tuple[str, str]:
    typed, typed_css = typing_name(user.upper())
    return typed + label("> ROLE: DEVELOPER", PAD, ROLE_Y, DIM, size=12), typed_css


def archive(width: float, cal: dict) -> tuple[str, str, str]:
    days = cal["days"]
    cols = max(d["col"] for d in days) + 1
    grid = {(d["row"], d["col"]): d for d in days}
    grid_w = cols * PITCH - GAP
    grid_h = ROWS * PITCH - GAP
    grid_x = (width - grid_w) / 2

    def cx(col: int) -> float:
        return grid_x + col * PITCH

    def cy(row: int) -> float:
        return GRID_Y + row * PITCH

    path = [(row, col) for col in range(cols)
            for row in (range(ROWS) if col % 2 == 0 else range(ROWS - 1, -1, -1))]
    eat_at = {p: i / (len(path) - 1) * WALK_END for i, p in enumerate(path)}

    css: list[str] = []
    rects: list[str] = []
    for (row, col), day in sorted(grid.items()):
        x, y = cx(col), cy(row)
        eat = eat_at[(row, col)]
        name = f"e{row}_{col}"
        if day["level"] == 0:
            css.append(f"@keyframes {name}{{0%,{pct(eat)}%{{fill:{LEVELS[0]}}}"
                       f"{pct(eat + 0.28)}%{{fill:{LEVELS[1]}}}"
                       f"{pct(eat + 2)}%,100%{{fill:{LEVELS[0]}}}}}")
            rects.append(f'<rect class="c" x="{x}" y="{y}" width="{CELL}" height="{CELL}"'
                         f' rx="2.4" fill="{LEVELS[0]}" style="animation-name:{name}"/>')
            continue

        colour = LEVELS[day["level"]]
        back = RESTORE_FROM + (col / max(cols - 1, 1)) * RESTORE_SPAN
        css.append(f"@keyframes {name}{{0%,{pct(eat)}%{{fill:{colour};transform:scale(1)}}"
                   f"{pct(eat + 0.28)}%{{fill:{FLASH};transform:scale(1.55)}}"
                   f"{pct(eat + 3)}%{{fill:{LEVELS[0]};transform:scale(1)}}"
                   f"{pct(back)}%{{fill:{LEVELS[0]}}}"
                   f"{pct(back + RESTORE_RISE)}%,100%{{fill:{colour}}}}}")
        rects.append(f'<rect class="c" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.4"'
                     f' fill="{colour}" style="animation-name:{name};'
                     f'transform-origin:{x + CELL / 2}px {y + CELL / 2}px"/>')

    def heading_at(i: int) -> int:
        a, b = (path[i], path[i + 1]) if i + 1 < len(path) else (path[i - 1], path[i])
        if b[1] != a[1]:
            return 90
        return 180 if b[0] > a[0] else 0

    walk = []
    for i, (row, col) in enumerate(path):
        walk.append(f"{pct(i / (len(path) - 1) * WALK_END)}%{{transform:translate("
                    f"{cx(col) + CELL / 2:.1f}px,{cy(row) + CELL / 2:.1f}px) rotate({heading_at(i)}deg)}}")
    last = heading_at(len(path) - 1)
    walk.append(f"{pct(HOLD_END)}%{{transform:translate("
                f"{cx(path[-1][1]) + CELL / 2:.1f}px,{cy(path[-1][0]) + CELL / 2:.1f}px) rotate({last}deg)}}")
    walk.append(f"{pct(RESET_AT)}%,100%{{transform:translate("
                f"{cx(path[0][1]) + CELL / 2:.1f}px,{cy(path[0][0]) + CELL / 2:.1f}px) rotate({heading_at(0)}deg)}}")
    css.append("@keyframes scuttle{0%,100%{transform:translateY(-.8px)}50%{transform:translateY(.8px)}}"
               "@keyframes feel{0%,100%{transform:rotate(-6deg)}50%{transform:rotate(6deg)}}"
               ".legs-a{animation:scuttle .22s linear infinite}"
               ".legs-b{animation:scuttle .22s linear infinite;animation-delay:-.11s}"
               ".feelers{animation:feel .9s ease-in-out infinite;transform-origin:0 -9px}")
    css.append("@keyframes walk{" + "".join(walk) + "}")
    css.append(f"@keyframes hero-fade{{0%,{pct(WALK_END + 1)}%{{opacity:1}}"
               f"{pct(HOLD_END)}%{{opacity:0}}{pct(RESET_AT + 4)}%{{opacity:0}}100%{{opacity:1}}}}")
    css.append("@keyframes beam{0%{transform:translateY(-30px)}"
               f"100%{{transform:translateY({grid_h + 30}px)}}}}")
    css.insert(0, f".c{{animation-duration:{DURATION};animation-timing-function:linear;"
                  "animation-iteration-count:infinite}"
                  f"#hero{{animation:walk {DURATION} linear infinite,"
                  f"hero-fade {DURATION} linear infinite}}"
                  "#beam{animation:beam 7s linear infinite}")

    def legs(sign: int, pairs: tuple[int, ...]) -> str:
        shapes = {
            0: "M{a}3.4 -3 L{a}7.4 -6.2 L{a}8.6 -9.6",
            1: "M{a}4 1 L{a}8.6 0.2 L{a}10.4 1.8",
            2: "M{a}3.6 5 L{a}7.6 8.2 L{a}8.4 11.6",
        }
        return "".join(shapes[k].format(a="-" if sign < 0 else "") for k in pairs)

    leg_a = "M" + " M".join(x[1:] for x in (legs(-1, (0,)), legs(1, (1,)), legs(-1, (2,))))
    leg_b = "M" + " M".join(x[1:] for x in (legs(1, (0,)), legs(-1, (1,)), legs(1, (2,))))
    hero = (
        f'<g id="hero" filter="url(#glow)"><circle r="15" fill="url(#lamp)"/>'
        f'<g fill="none" stroke="{FRAME}" stroke-width="1" stroke-linecap="round" stroke-linejoin="round">'
        f'<path class="legs-a" d="{leg_a}"/><path class="legs-b" d="{leg_b}"/>'
        f'<path class="feelers" d="M-1 -9.4 Q-4.5 -14 -8.5 -16.5 M1 -9.4 Q4.5 -14 8.5 -16.5"'
        f' stroke-width=".8"/></g>'
        f'<ellipse cy="2.2" rx="4.4" ry="6.8" fill="{SHADE}" stroke="{FRAME}" stroke-width="1.1"/>'
        f'<path d="M0 -1.6 V8.6" stroke="{FRAME}" stroke-width=".7"/>'
        f'<ellipse cy="-5" rx="3.8" ry="2.6" fill="{SHADE}" stroke="{FRAME}" stroke-width="1.1"/>'
        f'<ellipse cy="-8.3" rx="2" ry="1.5" fill="{FRAME}"/>'
        f'<circle cx="-1" cy="-8.6" r=".6" fill="{FLASH}"/><circle cx="1" cy="-8.6" r=".6" fill="{FLASH}"/>'
        f'</g>'
    )

    body = (
        f'<g opacity=".38"><g clip-path="url(#gridClip)">{"".join(rects)}'
        + f'<rect id="beam" x="{grid_x - 4}" y="{GRID_Y - 4}" width="{grid_w + 8}" height="26"'
        + f' fill="url(#beamGrad)" opacity=".16"/></g>'
        + hero + "</g>"
    )
    defs = (
        f'<radialGradient id="lamp"><stop offset="0" stop-color="{FRAME}" stop-opacity=".38"/>'
        f'<stop offset="1" stop-color="{FRAME}" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="beamGrad" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{FRAME}" stop-opacity="0"/>'
        f'<stop offset=".65" stop-color="{FRAME}" stop-opacity=".55"/>'
        f'<stop offset="1" stop-color="{FRAME}" stop-opacity="0"/></linearGradient>'
        f'<clipPath id="gridClip"><rect x="{grid_x - 4}" y="{GRID_Y - 4}"'
        f' width="{grid_w + 8}" height="{grid_h + 8}"/></clipPath>'
    )
    return body, "".join(css), defs


def diagnostic(width: float, profile: dict | None) -> tuple[str, str]:
    mid = width / 2
    right_start = mid + 30

    if profile:
        contrib = profile["contributionsCollection"]
        stats = [
            ("REPOSITORIES", profile["repositories"]["totalCount"]),
            ("STARS EARNED", sum(r["stargazerCount"] for r in profile["repositories"]["nodes"])),
            ("COMMITS 1Y", contrib["totalCommitContributions"]),
            ("PULL REQUESTS", contrib["totalPullRequestContributions"]),
            ("ISSUES", contrib["totalIssueContributions"]),
            ("FOLLOWERS", profile["followers"]["totalCount"]),
        ]
        langs = top_languages(profile)
    else:
        stats = [(k, "--") for k in ("REPOSITORIES", "STARS EARNED", "COMMITS 1Y",
                                     "PULL REQUESTS", "ISSUES", "FOLLOWERS")]
        langs = []

    rows = [
        heading(width, DIAG_HEAD_Y, "> SYSTEM DIAGNOSTIC"),
        f'<rect x="{mid}" y="{DIAG_HEAD_Y + 12}" width="1" height="{ROW_STEP * 6 + 12}"'
        f' fill="{FRAME}" opacity=".25"/>',
    ]

    for i, (name, value) in enumerate(stats):
        y = ROW_Y + i * ROW_STEP
        rows.append(label(name, PAD, y, DIM))
        rows.append(right(f"{value:,}".replace(",", " ") if isinstance(value, int) else str(value),
                          mid - 30, y, FRAME))

    bar_x, bar_w = right_start + 118, 128
    for i, (name, share) in enumerate(langs):
        y = ROW_Y + i * ROW_STEP
        rows.append(label(name[:12], right_start, y, DIM))
        rows.append(right(f"{share:.1f}%", width - PAD, y, FRAME))
        rows.append(f'<rect x="{bar_x}" y="{y - 8}" width="{bar_w}" height="8" rx="2"'
                    f' fill="{LEVELS[0]}"/>')
        rows.append(f'<rect class="bar" x="{bar_x}" y="{y - 8}" width="{bar_w * share / 100:.1f}"'
                    f' height="8" rx="2" fill="{FRAME}"'
                    f' style="transform-origin:{bar_x}px 0;animation-delay:{i * 0.12:.2f}s"/>')
        rows.append(f'<rect x="{bar_x}" y="{y - 8}" width="{bar_w}" height="8" rx="2"'
                    f' fill="url(#seg)"/>')

    if not langs:
        rows.append(label("NO DATA", right_start, ROW_Y, DIM))

    return "".join(rows), (f'<pattern id="seg" width="8" height="8" patternUnits="userSpaceOnUse">'
                           f'<rect x="6" width="2" height="8" fill="{BG}"/></pattern>')


def stand_by(width: float) -> str:
    cx, cy = width / 2, (SEC1_Y + SEC2_Y) / 2
    r_mid, r_in = 170, 96
    corners = "".join(
        f'<circle cx="{cx + dx}" cy="{cy + dy}" r="34" fill="none" stroke="{FRAME}" stroke-width="2"/>'
        f'<path d="M{cx + dx - 34} {cy + dy} H{cx + dx + 34} M{cx + dx} {cy + dy - 34} V{cy + dy + 34}"'
        f' stroke="{FRAME}" stroke-width="1"/>'
        for dx in (-(width / 2 - 90), width / 2 - 110) for dy in (HEIGHT - 120 - cy,)
    )
    def star(x: float, y: float, rad: float) -> str:
        pts = []
        for k in range(10):
            a = math.radians(k * 36)
            rr = rad if k % 2 == 0 else rad * 0.42
            pts.append(f"{x + rr * math.sin(a):.1f} {y - rr * math.cos(a):.1f}")
        return "M" + " L".join(pts) + " Z"

    r_star = (r_in + r_mid) / 2
    stars = " ".join(
        star(cx + r_star * math.sin(math.radians(a)), cy - r_star * math.cos(math.radians(a)), 11)
        for a in (i * 360 / 13 for i in range(13))
    )
    stripe_h = r_in * 2 / 9
    stripes = "".join(
        f'<rect x="{cx - r_in}" y="{cy - r_in + stripe_h * k:.1f}" width="{r_in * 2}"'
        f' height="{stripe_h:.1f}" fill="{FRAME}"/>'
        for k in range(0, 9, 2)
    )
    medallion = (
        f'<clipPath id="medal"><circle cx="{cx}" cy="{cy}" r="{r_in - 4}"/></clipPath>'
        f'<g opacity=".1"><path d="{stars}" fill="{FRAME}"/>'
        f'<g clip-path="url(#medal)">{stripes}</g></g>'
    )
    text = "PLEASE STAND BY"
    bw, bh = run_width(text, 30) + 44, 48
    return (
        '<g id="standby" opacity=".06">'
        f'<path d="M0 {cy} H{width} M{cx} 0 V{HEIGHT}" stroke="{FRAME}" stroke-width="1.2"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r_mid}" fill="none" stroke="{FRAME}" stroke-width="2"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r_in}" fill="none" stroke="{FRAME}" stroke-width="2"/>'
        f'{corners}'
        '</g>'
        + medallion
        + '<g id="standby-banner" opacity=".2">'
        f'<rect x="{cx - bw / 2:.1f}" y="{cy - bh / 2}" width="{bw:.1f}" height="{bh}" rx="6"'
        f' fill="none" stroke="{FRAME}" stroke-width="3"/>'
        + label(text, cx - run_width(text, 30) / 2, cy + 11, FRAME, size=30, weight="700")
        + "</g>"
    )


def render(user: str, profile: dict | None, cal: dict) -> str:
    cols = max(d["col"] for d in cal["days"]) + 1
    width = cols * PITCH - GAP + PAD * 2

    ident_body, ident_css = identity(user)
    scan_body, scan_css, scan_defs = archive(width, cal)
    diag_body, diag_defs = diagnostic(width, profile)

    body = "".join([
        scan_body, ident_body, rule(width, SEC1_Y),
        rule(width, SEC2_Y),
        diag_body,
        status_bar(width, profile, cal),
    ])

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{HEIGHT}" viewBox="0 0 {width} {HEIGHT}" role="img" aria-label="{html.escape(user)} profile rendered as a Vault-Tec terminal">
<defs>
<style>{BASE_CSS}{ident_css}{scan_css}</style>
<filter id="glow" x="-120%" y="-120%" width="340%" height="340%"><feGaussianBlur stdDeviation="1.7" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<filter id="softglow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="0.9" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<radialGradient id="vignette" cx=".5" cy=".5" r=".72"><stop offset=".5" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".7"/></radialGradient>
<pattern id="scan" width="4" height="3" patternUnits="userSpaceOnUse"><rect width="4" height="1" fill="{FRAME}" opacity=".07"/></pattern>
<clipPath id="screen"><rect x="3" y="3" width="{width - 6}" height="{HEIGHT - 6}" rx="10"/></clipPath>
{scan_defs}{diag_defs}</defs>
<rect width="{width}" height="{HEIGHT}" rx="12" fill="{BG}"/>
<g clip-path="url(#screen)">
{tab_bar(width)}
{body}
{stand_by(width)}
<rect width="{width}" height="{HEIGHT}" fill="url(#scan)"/>
<rect width="{width}" height="{HEIGHT}" fill="url(#vignette)"/>
<rect id="flicker" width="{width}" height="{HEIGHT}" fill="{FRAME}" opacity=".03"/>
</g>
<rect x="1" y="1" width="{width - 2}" height="{HEIGHT - 2}" rx="11" fill="none" stroke="{FRAME}" stroke-width="1" opacity=".45"/>
</svg>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--out", default="dist/terminal.svg")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    profile = graphql(args.user, token) if token else None
    if profile is None:
        print("falling back to the public contributions page", file=sys.stderr)
    cal = calendar_from_profile(profile) if profile else scrape_calendar(args.user)

    svg = render(args.user, profile, cal)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"wrote {out} ({len(svg) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
