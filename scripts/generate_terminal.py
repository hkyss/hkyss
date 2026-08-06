#!/usr/bin/env python3
"""Render a GitHub profile as a single Vault-Tec terminal readout."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PAD = 30
CELL = 11
GAP = 3
PITCH = CELL + GAP
ROWS = 7

TITLE_Y = 32
RULE_Y = 46
NAME_Y = 100
ROLE_Y = 128
SEC1_Y = 154
SCAN_HEAD_Y = 176
MONTH_Y = 196
GRID_Y = 204
SCAN_STAT_Y = 320
SEC2_Y = 334
DIAG_HEAD_Y = 356
ROW_Y = 382
ROW_STEP = 21
HEIGHT = 542

BG = "#0a0906"
FRAME = "#ffb642"
DIM = "#a8781f"
LEVELS = ["#2b2008", "#6b4708", "#a06a0c", "#d0921a", "#ffb642"]
FLASH = "#fff6dd"

DURATION = "30s"
WALK_END = 82.0
HOLD_END = 86.0
RESET_AT = 90.0
RESTORE_FROM = 90.0
RESTORE_SPAN = 6.0
RESTORE_RISE = 3.0

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

FONT = "ui-monospace,'SF Mono',SFMono-Regular,Menlo,Consolas,'Courier New',monospace"
ADVANCE = 8.2

BASE_CSS = (
    "#flicker{animation:flicker 3.1s steps(1,end) infinite}"
    ".caret{animation:caret 1.06s steps(1,end) infinite}"
    ".bar{animation:fill 9s ease-out infinite}"
    "#pulse{animation:pulse 1.4s ease-in-out infinite}"
    "@keyframes flicker{0%{opacity:.02}18%{opacity:.05}23%{opacity:.015}52%{opacity:.045}"
    "58%{opacity:.02}81%{opacity:.06}86%{opacity:.02}100%{opacity:.03}}"
    "@keyframes caret{0%{opacity:1}50%{opacity:0}100%{opacity:1}}"
    "@keyframes pulse{0%,100%{opacity:.35}50%{opacity:1}}"
    "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}"
    "@keyframes fill{0%{transform:scaleX(0)}16%,94%{transform:scaleX(1)}100%{transform:scaleX(0)}}"
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


def heading(width: float, y: float, left: str, tag: str) -> str:
    return label(left, PAD, y, FRAME, glow=True) + right(tag, width - PAD, y, DIM)


# --- data ---------------------------------------------------------------------

PROFILE_QUERY = """
query($login:String!){user(login:$login){
  createdAt
  followers{totalCount}
  contributionsCollection{
    totalCommitContributions totalPullRequestContributions totalIssueContributions
    contributionCalendar{totalContributions weeks{contributionDays{date contributionCount weekday}}}
  }
  repositories(first:100,ownerAffiliations:OWNER,isFork:false,privacy:PUBLIC){
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


def top_languages(profile: dict, limit: int = 6) -> list[tuple[str, float]]:
    totals: dict[str, int] = {}
    for repo in profile["repositories"]["nodes"]:
        for edge in repo["languages"]["edges"]:
            totals[edge["node"]["name"]] = totals.get(edge["node"]["name"], 0) + edge["size"]
    grand = sum(totals.values())
    if not grand:
        return []
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:limit]
    return [(name, size / grand * 100) for name, size in ranked]


# --- sections -----------------------------------------------------------------

def identity(width: float, user: str, profile: dict | None) -> tuple[str, str]:
    name = user.upper()
    cx, cy, r = width - PAD - 42, 98, 40

    teeth = "".join(
        f'<rect x="{cx - 4}" y="{cy - r - 5}" width="8" height="13" rx="1.5" fill="{FRAME}"'
        f' opacity=".75" transform="rotate({a},{cx},{cy})"/>'
        for a in range(0, 360, 45)
    )
    spokes = "".join(
        f'<path d="M{cx} {cy - 10} L{cx} {cy - r + 15}" stroke="{FRAME}" stroke-width="1.6"'
        f' transform="rotate({a},{cx},{cy})"/>'
        for a in (0, 120, 240)
    )
    emblem = (
        f'<circle cx="{cx}" cy="{cy}" r="{r + 7}" fill="none" stroke="{FRAME}"'
        f' stroke-width="1.2" opacity=".45"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r + 2}" fill="none" stroke="{FRAME}"'
        f' stroke-width="5" opacity=".1"/>'
        f'<g id="cog">{teeth}'
        f'<circle cx="{cx}" cy="{cy}" r="{r - 6}" fill="#120d04" stroke="{FRAME}" stroke-width="1.6"/>'
        f'<circle cx="{cx}" cy="{cy}" r="10" fill="none" stroke="{FRAME}" stroke-width="1.6"/>'
        f'{spokes}</g><circle cx="{cx}" cy="{cy}" r="3.2" fill="{FRAME}"/>'
    )

    since = profile["createdAt"][:10] if profile else "----------"
    body = (
        label(name + "_", PAD, NAME_Y, FRAME, size=44, glow=True, weight="700",
              inner=f'<tspan>{html.escape(name)}</tspan><tspan class="caret">_</tspan>')
        + label("> ROLE: DEVELOPER", PAD, ROLE_Y, DIM, size=12)
        + right(f"REG {since}", cx - r - 22, ROLE_Y, DIM, size=12)
        + emblem
    )
    return body, f"#cog{{animation:spin 24s linear infinite;transform-origin:{cx}px {cy}px}}"


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

    walk = []
    for i, (row, col) in enumerate(path):
        bob = -1.0 if i % 2 else 0.6
        walk.append(f"{pct(i / (len(path) - 1) * WALK_END)}%{{transform:translate("
                    f"{cx(col) + CELL / 2:.1f}px,{cy(row) + CELL / 2 + bob:.1f}px)}}")
    walk.append(f"{pct(HOLD_END)}%{{transform:translate("
                f"{cx(path[-1][1]) + CELL / 2:.1f}px,{cy(path[-1][0]) + CELL / 2:.1f}px)}}")
    walk.append(f"{pct(RESET_AT)}%,100%{{transform:translate("
                f"{cx(path[0][1]) + CELL / 2:.1f}px,{cy(path[0][0]) + CELL / 2:.1f}px)}}")
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

    months = []
    last_month, last_col = None, -9
    for col in range(cols):
        day = next((grid[(r, col)] for r in range(ROWS) if (r, col) in grid), None)
        if not day:
            continue
        month = int(day["date"][5:7])
        text, x = MONTHS[month - 1], cx(col)
        if month != last_month and col - last_col >= 3 and x + run_width(text, 9) <= width - PAD:
            months.append(label(text, x, MONTH_Y, DIM, size=9))
            last_month, last_col = month, col

    hero = (
        f'<g id="hero" filter="url(#glow)"><circle r="15" fill="url(#lamp)"/>'
        f'<path d="M-6.5 4.5 L-6.5 -1.5 A6.5 6.5 0 0 1 6.5 -1.5 L6.5 4.5 Z" fill="#120d04"'
        f' stroke="{FRAME}" stroke-width="1.1" stroke-linejoin="round"/>'
        f'<path d="M-7.4 4.6 L7.4 4.6 L6.2 7.8 L-6.2 7.8 Z" fill="#120d04" stroke="{FRAME}"'
        f' stroke-width="1.1" stroke-linejoin="round"/>'
        f'<rect x="-5.4" y="-1.4" width="10.8" height="4.6" rx="1.3" fill="#080502"'
        f' stroke="{FRAME}" stroke-width=".8"/>'
        f'<circle cx="-2.6" cy="1" r="1.35" fill="{FLASH}"/>'
        f'<circle cx="2.6" cy="1" r="1.35" fill="{FLASH}"/>'
        f'<path d="M5.6 -4.6 L8.6 -9.4" stroke="{FRAME}" stroke-width="1" fill="none"/>'
        f'<circle id="pulse" cx="8.9" cy="-10" r="1.4" fill="{FRAME}"/></g>'
    )

    dates = sorted(d["date"] for d in days)
    total = f"{cal['total']:,}".replace(",", " ") if cal["total"] else "--"
    logged = sum(1 for d in days if d["level"] > 0)
    current, longest = streaks(days)

    body = (
        heading(width, SCAN_HEAD_Y, "> ARCHIVE :: CONTRIBUTION SCAN", f"{dates[0]} / {dates[-1]}")
        + "".join(months)
        + f'<g clip-path="url(#gridClip)">{"".join(rects)}'
        + f'<rect id="beam" x="{grid_x - 4}" y="{GRID_Y - 4}" width="{grid_w + 8}" height="26"'
        + f' fill="url(#beamGrad)" opacity=".16"/></g>'
        + hero
        + label(f"> {total} ENTRIES / {logged} ACTIVE DAYS", PAD, SCAN_STAT_Y, DIM)
        + right(f"STREAK {current} :: MAX {longest}", width - PAD, SCAN_STAT_Y, FRAME)
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
        heading(width, DIAG_HEAD_Y, "> SYSTEM DIAGNOSTIC", "V.A.T.S. READOUT"),
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


def render(user: str, profile: dict | None, cal: dict) -> str:
    cols = max(d["col"] for d in cal["days"]) + 1
    width = cols * PITCH - GAP + PAD * 2

    ident_body, ident_css = identity(width, user, profile)
    scan_body, scan_css, scan_defs = archive(width, cal)
    diag_body, diag_defs = diagnostic(width, profile)

    repos = str(profile["repositories"]["totalCount"]) if profile else "--"
    boot = f"> SYSTEM BOOT COMPLETE :: {repos} REPOSITORIES ONLINE "
    body = "".join([
        ident_body, rule(width, SEC1_Y),
        scan_body, rule(width, SEC2_Y),
        diag_body,
        rule(width, HEIGHT - 34),
        label(boot + "_", PAD, HEIGHT - 16, FRAME, glow=True,
              inner=f'<tspan>{html.escape(boot)}</tspan><tspan class="caret">_</tspan>'),
        right("LINK ESTABLISHED", width - PAD, HEIGHT - 16, DIM),
    ])

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{HEIGHT}" viewBox="0 0 {width} {HEIGHT}" role="img" aria-label="{html.escape(user)} profile rendered as a Vault-Tec terminal">
<defs>
<style>{BASE_CSS}{ident_css}{scan_css}</style>
<filter id="glow" x="-120%" y="-120%" width="340%" height="340%"><feGaussianBlur stdDeviation="1.7" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<filter id="softglow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="0.9" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<radialGradient id="vignette" cx=".5" cy=".5" r=".78"><stop offset=".55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".55"/></radialGradient>
<pattern id="scan" width="4" height="3" patternUnits="userSpaceOnUse"><rect width="4" height="1" fill="{FRAME}" opacity=".07"/></pattern>
<clipPath id="screen"><rect x="3" y="3" width="{width - 6}" height="{HEIGHT - 6}" rx="10"/></clipPath>
{scan_defs}{diag_defs}</defs>
<rect width="{width}" height="{HEIGHT}" rx="12" fill="{BG}"/>
<g clip-path="url(#screen)">
{heading(width, TITLE_Y, "ROBCO INDUSTRIES (TM) TERMLINK PROTOCOL", "PIPBOY 3000 MK IV")}
{rule(width, RULE_Y)}
{body}
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
