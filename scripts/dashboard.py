"""Draw the profile README as a set of dark dashboard panels (SVG).

Writes to <output_dir>:
  hero.svg          intro + recently updated repos
  activity.svg      contributions chart + overview numbers
  languages.svg     language share
  snake.svg         the Platane/snk contribution snake, recoloured and framed as a panel
  project-<n>.svg   one row per recently updated public repo (newest first)

Data comes from the GitHub API. Private contributions are counted when
"Include private contributions on my profile" is turned on.

With --readme, the links around the project rows (between the
<!-- projects:start --> and <!-- projects:end --> markers) are rewritten to
match the repos drawn, since those change as repos are updated.

Usage: GITHUB_TOKEN=... python dashboard.py <username> <output_dir>
                                            [--snake snk.svg] [--readme README.md]
"""
import base64
import datetime as dt
import json
import math
import os
import random
import re
import sys
import tempfile
import urllib.request
from collections import defaultdict
from html import escape

# ---------------------------------------------------------------- content

NAME = "Mikey"
AKA = "aka Bot"
INTRO = [
    "Michael, a developer from the Czech Republic.",
    "10+ years of programming. Game development (#GodotOnTop)",
    "and cybersecurity on the side.",
]
STACK = ["Python", "C#", "Node.js", "JavaScript", "C++", "Godot", "Git"]

PROJECT_COUNT = 4
DISPLAY_NAMES = {"AndroidExplorer": "Android Explorer", "godot-game-builder": "Godot Game Builder"}

# ---------------------------------------------------------------- palette

# Each panel is drawn twice: dark (shown in GitHub's dark theme) and light ("-light" files).
# INK is the colour of the faint overlay lines/fills (borders, grid, ticks): white on dark, black on light.
THEMES = {
    "dark": dict(BG_A="#1c1d20", BG_B="#0e0f11", TEXT="#ececec", DIM="#8e8f94", FAINT="#5c5d62",
                 ACCENT="#ff3b3b", GREEN="#7bd88f", INK="#fff", TIP="#0b0b0c",
                 SNAKE_COLORS={"--ce": "#1f2023", "--c0": "#1f2023", "--c1": "#4a1719", "--c2": "#7d2124",
                               "--c3": "#bb2c2f", "--c4": "#ff3b3b", "--cb": "#ffffff08", "--cs": "#ff3b3b"}),
    "light": dict(BG_A="#ffffff", BG_B="#f1f2f4", TEXT="#1f2328", DIM="#59636e", FAINT="#8c959f",
                  ACCENT="#e5262d", GREEN="#1a7f37", INK="#000", TIP="#ffffff",
                  SNAKE_COLORS={"--ce": "#e6e8eb", "--c0": "#e6e8eb", "--c1": "#ffd3d1", "--c2": "#ff9e9a",
                                "--c3": "#f25a5c", "--c4": "#e5262d", "--cb": "#1b1f230d", "--cs": "#e5262d"}),
}


def set_theme(name):
    globals().update(THEMES[name])


def themed(svg):
    """Swap the overlay ink (written as "#fff" in the drawing code) for the current theme's."""
    return svg.replace('"#fff"', f'"{INK}"').replace('"#0b0b0c"', f'"{TIP}"')


set_theme("dark")
FONT_STACK = "O,'Segoe UI','Helvetica Neue',Helvetica,Arial,sans-serif"

# ---------------------------------------------------------------- api

TOKEN = None


def _request(url, data=None):
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"bearer {TOKEN}", "Content-Type": "application/json",
        "User-Agent": "profile-dashboard", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def gql(query, **variables):
    data = _request("https://api.github.com/graphql",
                    json.dumps({"query": query, "variables": variables}).encode())
    if data.get("errors"):
        raise SystemExit(f"GraphQL error: {data['errors']}")
    return data["data"]


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Prague")
except Exception:  # no tz database (e.g. bare Windows Python)
    LOCAL_TZ = dt.timezone.utc


def local(t):
    return t.astimezone(LOCAL_TZ)


def fetch_contributions(user):
    base = gql("""query($login:String!){user(login:$login){createdAt followers{totalCount}
        repositories(ownerAffiliations:OWNER, privacy:PUBLIC, isFork:false, first:100){totalCount
          nodes{stargazerCount languages(first:10, orderBy:{field:SIZE, direction:DESC}){
            edges{size node{name color}}}}}}}""", login=user)["user"]
    created = parse_time(base["createdAt"])
    now = dt.datetime.now(dt.timezone.utc)

    days, all_time = {}, 0
    for year in range(created.year, now.year + 1):
        start = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
        end = min(dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc), now)
        cal = gql("""query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login){
            contributionsCollection(from:$from,to:$to){contributionCalendar{totalContributions
            weeks{contributionDays{date contributionCount}}}}}}""",
                  login=user, **{"from": start.isoformat(), "to": end.isoformat()}
                  )["user"]["contributionsCollection"]["contributionCalendar"]
        all_time += cal["totalContributions"]
        for week in cal["weeks"]:
            for d in week["contributionDays"]:
                days[dt.date.fromisoformat(d["date"])] = d["contributionCount"]

    last_year = gql("""query($login:String!){user(login:$login){contributionsCollection{
        contributionCalendar{totalContributions} restrictedContributionsCount}}}""",
                    login=user)["user"]["contributionsCollection"]

    today = now.date()
    ordered = sorted(d for d in days if d <= today)
    longest = run = 0
    for d in ordered:
        run = run + 1 if days[d] else 0
        longest = max(longest, run)
    current = 0
    for i, d in enumerate(reversed(ordered)):
        if days[d]:
            current += 1
        elif i == 0:
            continue  # nothing yet today; a streak ending yesterday still counts
        else:
            break

    # 52 weekly totals ending today (oldest first) and the last 365 days.
    weeks = []
    for w in range(51, -1, -1):
        end = today - dt.timedelta(days=7 * w)
        start = end - dt.timedelta(days=6)
        weeks.append((start, sum(days.get(start + dt.timedelta(days=i), 0) for i in range(7))))
    daily = [days.get(today - dt.timedelta(days=i), 0) for i in range(364, -1, -1)]

    lang_size, lang_color = defaultdict(int), {}
    for repo in base["repositories"]["nodes"]:
        for e in repo["languages"]["edges"]:
            lang_size[e["node"]["name"]] += e["size"]
            lang_color[e["node"]["name"]] = e["node"]["color"] or DIM
    total = sum(lang_size.values()) or 1
    top = sorted(lang_size.items(), key=lambda kv: -kv[1])[:6]

    return {
        "last_year": last_year["contributionCalendar"]["totalContributions"],
        "private": last_year["restrictedContributionsCount"],
        "all_time": all_time,
        "since": created.year,
        "current_streak": current,
        "longest_streak": longest,
        "this_week": sum(daily[-7:]),
        "active_30": sum(1 for n in daily[-30:] if n),
        "daily": daily,
        "weeks": weeks,
        "repos": base["repositories"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in base["repositories"]["nodes"]),
        "followers": base["followers"]["totalCount"],
        "languages": [(name, size / total, lang_color[name]) for name, size in top],
        "today": today,
    }


def fetch_repos(user, limit=PROJECT_COUNT):
    """The most recently pushed public, non-fork repos (excluding this profile repo), newest first."""
    nodes = gql("""query($login:String!){user(login:$login){repositories(ownerAffiliations:OWNER,
        privacy:PUBLIC, isFork:false, first:12, orderBy:{field:PUSHED_AT, direction:DESC}){nodes{
        name description url pushedAt createdAt isArchived stargazerCount latestRelease{tagName}
        languages(first:8, orderBy:{field:SIZE, direction:DESC}){totalSize edges{size node{name}}}
        defaultBranchRef{target{... on Commit{messageHeadline committedDate history{totalCount}}}}}}}}""",
                login=user)["user"]["repositories"]["nodes"]
    repos = []
    for r in nodes:
        if r["name"].lower() == user.lower():
            continue
        commit = (r.get("defaultBranchRef") or {}).get("target") or {}
        total = r["languages"]["totalSize"] or 1
        repos.append({
            "repo": r["name"],
            "url": r["url"],
            "name": DISPLAY_NAMES.get(r["name"], r["name"]),
            "description": r["description"] or "",
            "pushed": local(parse_time(r["pushedAt"])),
            "created": local(parse_time(r["createdAt"])),
            "committed": local(parse_time(commit.get("committedDate") or r["pushedAt"])),
            "headline": commit.get("messageHeadline", ""),
            "archived": r["isArchived"],
            "stars": r["stargazerCount"],
            "release": (r["latestRelease"] or {}).get("tagName"),
            "languages": [(e["node"]["name"], e["size"] / total) for e in r["languages"]["edges"]],
            "commit_total": (commit.get("history") or {}).get("totalCount", 0),
        })
        if len(repos) == limit:
            break
    return repos


# ---------------------------------------------------------------- font

def load_font(cache_dir):
    """Embed Outfit (OFL, Google Fonts) so the panels look the same everywhere."""
    path = os.path.join(cache_dir, "outfit-latin.woff2")
    if not os.path.exists(path):
        try:
            css = urllib.request.urlopen(urllib.request.Request(
                "https://fonts.googleapis.com/css2?family=Outfit:wght@200..600",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}),
                timeout=30).read().decode()
            url = re.search(r"/\* latin \*/[^}]*?url\((https://[^)]+\.woff2)\)", css).group(1)
            data = urllib.request.urlopen(url, timeout=30).read()
            os.makedirs(cache_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        except Exception as exc:
            print(f"font unavailable, using system fonts: {exc}")
            return ""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return (f"@font-face{{font-family:O;font-weight:200 600;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")


FONT_CSS = ""

# ---------------------------------------------------------------- drawing helpers

_NARROW = set("iljtfr.,:;'|!I1 ()[]")
_WIDE = set("mwMW@%")


def text_width(s, size):
    w = 0.0
    for ch in s:
        if ch in _NARROW:
            w += 0.3
        elif ch in _WIDE:
            w += 0.82
        elif ch.isupper() or ch.isdigit():
            w += 0.6
        else:
            w += 0.52
    return w * size


def fmt(n):
    return f"{n:,}"


def date_str(t, with_time=False):
    return t.strftime("%d.%m.%Y, %H:%M" if with_time else "%d.%m.%Y")


def t(x, y, s, size=14, fill=None, weight=300, anchor="start", extra=""):
    fill = fill or TEXT
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{extra}>{escape(str(s))}</text>')


def caps(x, y, s, anchor="start"):
    return t(x, y, s.upper(), 11.5, FAINT, 400, anchor, ' letter-spacing="2"')


def open_svg(w, h, label, glow=(0.0, 0.0)):
    gx, gy = glow
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{escape(label)}">',
        f"<style>{FONT_CSS}text{{font-family:{FONT_STACK}}}"
        "@keyframes fade{from{opacity:0}to{opacity:1}}.f{animation:fade .9s ease-out both}"
        "@keyframes draw{from{stroke-dashoffset:var(--l)}to{stroke-dashoffset:0}}"
        ".d{stroke-dasharray:var(--l);animation:draw 1.8s cubic-bezier(.3,.6,.2,1) both}"
        "@keyframes pulse{0%{r:4;opacity:.7}100%{r:13;opacity:0}}.p{animation:pulse 2.4s ease-out infinite}"
        "</style>",
        "<defs>",
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG_A}"/>'
        f'<stop offset="1" stop-color="{BG_B}"/></linearGradient>',
        f'<radialGradient id="glow" cx="{gx}" cy="{gy}" r="0.9"><stop offset="0" stop-color="#ffffff" stop-opacity=".07"/>'
        '<stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>',
        f'<linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{ACCENT}" stop-opacity=".32"/>'
        f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></linearGradient>',
        "</defs>",
        f'<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="22" fill="url(#bg)"/>',
        f'<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="22" fill="url(#glow)" stroke="#fff" stroke-opacity=".08"/>',
        corner_cubes(w, h, seed=label),
    ]


def corner_cubes(w, h, x0=0.0, y0=0.0, k=1.0, seed=""):
    """Faint grid of squares growing out of the bottom-left corner and fading away.

    Seeded by the panel so the pattern stays the same from day to day.
    """
    rnd = random.Random(seed)
    step, size, inset = 16 * k, 9 * k, 18 * k
    reach = min(270 * k, h * 1.15)
    out = ['<g shape-rendering="crispEdges">']
    n = int(reach / step) + 1
    for i in range(n):
        for j in range(n):
            d = math.hypot(i * step, j * step)
            if d > reach:
                continue
            fade = (1 - d / reach) ** 1.8
            roll = rnd.random()
            x, y = x0 + inset + i * step, y0 + h - inset - size - j * step
            if roll < 0.28:
                continue
            if roll > 0.965:
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" rx="{1.5*k:.1f}" '
                           f'fill="{ACCENT}" fill-opacity="{.15 * fade:.3f}"/>')
            elif roll > 0.75:
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" rx="{1.5*k:.1f}" '
                           f'fill="#fff" fill-opacity="{.03 * fade:.3f}"/>')
            else:
                out.append(f'<rect x="{x+.5:.1f}" y="{y+.5:.1f}" width="{size-1:.1f}" height="{size-1:.1f}" '
                           f'rx="{1.5*k:.1f}" fill="none" stroke="#fff" stroke-opacity="{.045 * fade:.3f}"/>')
    out.append("</g>")
    return "".join(out)


def pill(x, y, label, dot=None, size=12.5, color=None, h=26):
    color = color or DIM
    tw = text_width(label, size)
    pad = 13
    w = tw + pad * 2 + (14 if dot else 0)
    o = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="{h/2}" fill="#fff" fill-opacity=".035" '
         f'stroke="#fff" stroke-opacity=".12"/>']
    tx = x + pad
    if dot:
        o.append(f'<circle cx="{tx+3.5:.1f}" cy="{y+h/2:.1f}" r="3.5" fill="{dot}"/>')
        tx += 14
    o.append(t(tx, y + h / 2 + size * 0.36, label, size, color))
    return "".join(o), w


def star_pill(x, y, n, h=26):
    """Pill with a drawn star (the font has no star glyph)."""
    label = str(n)
    w = 13 + 12 + 6 + text_width(label, 12.5) + 13
    cx, cy, r = x + 19, y + h / 2, 5.6
    pts = " ".join(f"{cx + (r if i % 2 == 0 else r * .45) * math.sin(i * math.pi / 5):.2f},"
                   f"{cy - (r if i % 2 == 0 else r * .45) * math.cos(i * math.pi / 5):.2f}" for i in range(10))
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="{h/2}" fill="#fff" fill-opacity=".035" '
            f'stroke="#fff" stroke-opacity=".12"/><polygon points="{pts}" fill="none" stroke="{DIM}" '
            f'stroke-width="1.1" stroke-linejoin="round"/>' + t(x + 31, y + h / 2 + 4.5, label, 12.5, DIM)), w


def arrow(x, y, s=11, color=None):
    color = color or DIM
    return (f'<path d="M{x} {y+s} L{x+s} {y} M{x+2.5} {y} H{x+s} V{y+s-2.5}" fill="none" stroke="{color}" '
            f'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>')


def smooth(points):
    """Monotone cubic (Fritsch-Carlson) path through points: smooth, never overshoots."""
    n = len(points)
    if n < 2:
        return ""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [d[0]] + [0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            h = a * a + b * b
            if h > 9:
                k = 3 / math.sqrt(h)
                m[i], m[i + 1] = k * a * d[i], k * b * d[i]
    out = [f"M{xs[0]:.1f} {ys[0]:.1f}"]
    for i in range(n - 1):
        dx = (xs[i + 1] - xs[i]) / 3
        out.append(f"C{xs[i] + dx:.1f} {ys[i] + m[i] * dx:.1f} {xs[i + 1] - dx:.1f} "
                   f"{ys[i + 1] - m[i + 1] * dx:.1f} {xs[i + 1]:.1f} {ys[i + 1]:.1f}")
    return " ".join(out)


def path_len(points):
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)) * 1.08 + 10


def clip(s, width, size):
    if text_width(s, size) <= width:
        return s
    while s and text_width(s + "…", size) > width:
        s = s[:-1]
    return s.rstrip() + "…"


def wrap(s, width, size, lines=2):
    words, out, cur = s.split(), [], ""
    for wd in words:
        trial = f"{cur} {wd}".strip()
        if text_width(trial, size) <= width:
            cur = trial
        else:
            out.append(cur)
            cur = wd
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        while text_width(out[-1] + "…", size) > width and " " in out[-1]:
            out[-1] = out[-1].rsplit(" ", 1)[0]
        out[-1] = out[-1].rstrip(".,:;") + "…"
    return [line for line in out if line]


# ---------------------------------------------------------------- panels

def hero(repos):
    W, H = 1000, 290
    o = open_svg(W, H, f"{NAME}, {AKA}. " + " ".join(INTRO), glow=(0.05, 0.0))
    o.append('<g class="f">')
    o.append(caps(40, 52, "Hello, I'm"))
    o.append(t(36, 124, NAME, 76, TEXT, 200, extra=' letter-spacing="-1"'))
    o.append(t(40 + text_width(NAME, 76) * 0.94, 124, AKA, 22, FAINT, 300))
    for i, line in enumerate(INTRO):
        o.append(t(40, 162 + i * 22, line, 15.5, DIM))
    x = 40
    for name in STACK:
        s, w = pill(x, 226, name)
        o.append(s)
        x += w + 8
    o.append("</g>")

    # Right column: recently updated public repos, drawn like an alert list.
    rx = 662
    o.append(f'<line x1="{rx-26}" y1="36" x2="{rx-26}" y2="{H-36}" stroke="#fff" stroke-opacity=".07"/>')
    o.append(t(rx, 54, "Recently updated", 16, TEXT, 300))
    if not repos:
        o.append(t(rx, 96, "Nothing public lately.", 13.5, FAINT))
    for i, r in enumerate(repos[:4]):
        y = 92 + i * 47
        o.append(f'<g class="f" style="animation-delay:{.15 + i * .12:.2f}s">')
        o.append(f'<line x1="{rx+1}" y1="{y-10}" x2="{rx+1}" y2="{y+22}" stroke="{ACCENT if i == 0 else "#fff"}" '
                 f'stroke-opacity="{1 if i == 0 else .18}" stroke-width="1.5"/>')
        o.append(t(rx + 16, y + 4, clip(r["name"], 200, 14.5), 14.5, TEXT))
        o.append(t(W - 36, y + 4, date_str(r["committed"]), 12, FAINT, 300, "end"))
        o.append(t(rx + 16, y + 21, clip(r["headline"], 280, 12), 12, FAINT))
        o.append("</g>")
    o.append("</svg>")
    return "\n".join(o)


def activity(s):
    W, H = 1000, 400
    o = open_svg(W, H, f"{fmt(s['last_year'])} contributions in the last 12 months", glow=(0.0, 0.0))

    # Left: big number + weekly area chart.
    o.append(caps(40, 52, "Contributions"))
    o.append(caps(600, 52, "last 12 months", "end"))
    big = fmt(s["last_year"])
    o.append(t(36, 122, big, 62, TEXT, 200, extra=' letter-spacing="-1"'))
    bx = 40 + text_width(big, 62) * 0.93
    if s["this_week"]:
        o.append(t(bx, 90, f"+{s['this_week']}", 20, ACCENT, 300))
    o.append(t(40, 150, f"contributions · {s['this_week']} this week", 14, DIM))

    x0, x1, y0, y1 = 40, 600, 186, 334
    weeks = s["weeks"]
    peak = max(n for _, n in weeks) or 1
    top = max(4, math.ceil(peak * 1.15 / 4) * 4)

    # Alternating month columns + gridlines.
    step = (x1 - x0) / (len(weeks) - 1)
    month_starts = [i for i in range(len(weeks)) if i == 0 or weeks[i][0].month != weeks[i - 1][0].month]
    for k, i in enumerate(month_starts):
        xa = x0 + i * step
        xb = x0 + (month_starts[k + 1] * step if k + 1 < len(month_starts) else x1 - x0)
        if k % 2:
            o.append(f'<rect x="{xa:.1f}" y="{y0}" width="{xb-xa:.1f}" height="{y1-y0}" fill="#fff" fill-opacity=".018"/>')
        if xb - xa > 26:
            o.append(t((xa + xb) / 2, y1 + 22, weeks[i][0].strftime("%b").upper(), 10.5, FAINT, 400, "middle",
                       ' letter-spacing="1"'))
    for k in range(5):
        y = y1 - (y1 - y0) * k / 4
        dash = "" if k == 0 else ' stroke-dasharray="3 5"'
        o.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="#fff" '
                 f'stroke-opacity="{.09 if k == 0 else .045}"{dash}/>')
        if k:
            o.append(t(x1 + 10, y + 3.5, top * k // 4, 10.5, FAINT, 300))

    pts = [(x0 + i * step, y1 - (y1 - y0) * n / top) for i, (_, n) in enumerate(weeks)]
    line = smooth(pts)
    o.append(f'<path class="f" d="{line} L{x1} {y1} L{x0} {y1} Z" fill="url(#area)" style="animation-delay:.6s"/>')
    o.append(f'<path class="d" style="--l:{path_len(pts):.0f}" d="{line}" fill="none" stroke="{ACCENT}" '
             'stroke-width="1.8" stroke-linejoin="round"/>')

    # Peak marker with tooltip.
    pi = max(range(len(weeks)), key=lambda i: weeks[i][1])
    px, py = pts[pi]
    if weeks[pi][1]:
        o.append(f'<g class="f" style="animation-delay:1.6s">')
        o.append(f'<line x1="{px:.1f}" y1="{y0-6}" x2="{px:.1f}" y2="{y1}" stroke="#fff" stroke-opacity=".55" '
                 'stroke-dasharray="3 4"/>')
        o.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="#fff"/>')
        bw, bh = 150, 46
        bx = px + 12 if px + 12 + bw < x1 else px - 12 - bw
        by = max(y0 - 4, py - bh - 10) if py - bh - 10 > y0 - 4 else py + 12
        o.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw}" height="{bh}" rx="8" fill="#0b0b0c" fill-opacity=".9" '
                 'stroke="#fff" stroke-opacity=".1"/>')
        o.append(t(bx + 12, by + 19, f"week of {date_str(dt.datetime.combine(weeks[pi][0], dt.time()))}", 12, TEXT))
        o.append(f'<rect x="{bx+12:.1f}" y="{by+28:.1f}" width="7" height="7" rx="1.5" fill="{ACCENT}"/>')
        o.append(t(bx + 25, by + 35, f"{weeks[pi][1]} contributions", 11.5, DIM))
        o.append("</g>")

    lx, ly = pts[-1]
    o.append(f'<circle class="p" cx="{lx:.1f}" cy="{ly:.1f}" r="4" fill="none" stroke="{ACCENT}"/>')
    o.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{ACCENT}"/>')

    # Right: overview.
    rx, rw = 660, 304
    o.append(f'<line x1="{rx-24}" y1="36" x2="{rx-24}" y2="{H-36}" stroke="#fff" stroke-opacity=".07"/>')
    o.append(t(rx, 54, "Overview", 16, TEXT))
    o.append(t(rx + rw, 54, f"since {s['since']}", 12, FAINT, 300, "end"))

    # Active-days inset (like an uptime meter).
    iy = 74
    o.append(f'<rect x="{rx}" y="{iy}" width="{rw}" height="92" rx="12" fill="#fff" fill-opacity=".03" '
             'stroke="#fff" stroke-opacity=".06"/>')
    o.append(t(rx + 14, iy + 24, "Active days · 30 days", 12.5, DIM))
    pct = round(100 * s["active_30"] / 30)
    o.append(t(rx + rw - 14, iy + 25, f"{pct}%", 16, TEXT, 400, "end"))
    last30 = s["daily"][-30:]
    m30 = max(last30) or 1
    sp = [(rx + 14 + i * (rw - 28) / 29, iy + 78 - 34 * n / m30) for i, n in enumerate(last30)]
    o.append(f'<path class="d" style="--l:{path_len(sp):.0f};animation-delay:.4s" d="{smooth(sp)}" fill="none" '
             f'stroke="{ACCENT}" stroke-width="1.4"/>')
    o.append(f'<line x1="{rx+14}" y1="{iy+79}" x2="{rx+rw-14}" y2="{iy+79}" stroke="#fff" stroke-opacity=".08"/>')

    rows = [
        ("All-time contributions", fmt(s["all_time"])),
        ("Current streak", f"{s['current_streak']} day{'s' if s['current_streak'] != 1 else ''}"),
        ("Longest streak", f"{s['longest_streak']} days"),
        ("Public repositories", fmt(s["repos"])),
        ("Stars earned", fmt(s["stars"])),
    ]
    if s["private"]:
        rows.insert(1, ("Private contributions", fmt(s["private"])))
    rows = rows[:6]
    ry = 190
    rh = (H - 36 - ry) / len(rows)
    for i, (label, val) in enumerate(rows):
        y = ry + i * rh
        if i:
            o.append(f'<line x1="{rx}" y1="{y:.1f}" x2="{rx+rw}" y2="{y:.1f}" stroke="#fff" stroke-opacity=".07"/>')
        o.append(t(rx + 2, y + rh / 2 + 5, label, 13.5, DIM))
        o.append(t(rx + rw - 2, y + rh / 2 + 5, val, 14, TEXT, 400, "end"))
    o.append("</svg>")
    return "\n".join(o)


def languages(s):
    W, H = 1000, 230
    langs = s["languages"][:6] or [("—", 1.0, DIM)]
    o = open_svg(W, H, "Languages: " + ", ".join(f"{n} {p*100:.0f}%" for n, p, _ in langs), glow=(0.9, 0.0))
    o.append(caps(40, 50, "Languages"))

    # Faint daily contribution spikes behind, like a waveform.
    daily = s["daily"]
    m = max(daily) or 1
    x0, x1, base = 40, W - 40, 196
    spikes = []
    for i, n in enumerate(daily):
        x = x0 + i * (x1 - x0) / (len(daily) - 1)
        spikes.append(f"M{x:.1f} {base} V{base - 4 - 96 * n / m:.1f}" if n else f"M{x:.1f} {base} V{base-2}")
    o.append(f'<path d="{" ".join(spikes)}" stroke="#fff" stroke-opacity=".07" stroke-width="1.2"/>')

    # Stepped segments, biggest language highest: width loosely follows share, each step a little lower.
    n = len(langs)
    total = x1 - x0
    minw = 112
    free = max(0, total - minw * n - 14 * (n - 1))
    x = x0
    for i, (name, share, color) in enumerate(langs):
        w = minw + free * share / sum(p for _, p, _ in langs)
        y = 110 + i * (70 / max(1, n - 1))
        lead = i == 0
        stroke = ACCENT if lead else "#fff"
        o.append(f'<g class="f" style="animation-delay:{i * .12:.2f}s">')
        o.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x+w:.1f}" y2="{y:.1f}" stroke="{stroke}" '
                 f'stroke-opacity="{1 if lead else .85}" stroke-width="{2.4 if lead else 1.8}" stroke-linecap="round"/>')
        o.append(f'<circle cx="{x+w:.1f}" cy="{y:.1f}" r="2.6" fill="{stroke}"/>')
        o.append(t(x, y - 12, name, 21, TEXT, 300))
        o.append(t(x, y + 21, f"{share*100:.1f}%", 13, ACCENT if lead else DIM, 300))
        o.append("</g>")
        x += w + 14
    o.append("</svg>")
    return "\n".join(o)


def project_row(p, today):
    """Compact full-width row: name and description on the left; pills and language band on the right."""
    W, H = 1000, 132
    o = open_svg(W, H, f"{p['name']}: {p['description']}", glow=(0.0, 0.0))
    o.append(arrow(W - 40, 26))

    # Text block (name, meta line, 1-2 description lines), centred vertically in the row.
    tx = 40
    desc = wrap(p["description"] or "No description yet.", 540, 13.5)
    block = 17 + 22 + 26 + 19 * (len(desc) - 1) + 4  # name cap height .. last line's descender
    y = (H - block) / 2 + 17
    o.append(t(tx, y, p["name"], 23, TEXT, 300))
    commits = p["commit_total"]
    o.append(t(tx, y + 22, f"updated {date_str(p['pushed'], True)} · {commits} commit{'s' if commits != 1 else ''} · "
                           f"since {p['created'].strftime('%m.%Y')}", 12, FAINT))
    for i, line in enumerate(desc):
        o.append(t(tx, y + 48 + i * 19, line, 13.5, DIM))

    rx, rx1 = 640, W - 70
    o.append(f'<line x1="{rx-26}" y1="26" x2="{rx-26}" y2="{H-26}" stroke="#fff" stroke-opacity=".07"/>')

    # Pills: status, release, stars.
    recent = (today - p["pushed"].date()).days <= 30
    if p["archived"]:
        chips = [lambda x: pill(x, 24, "Archived")]
    elif recent:
        chips = [lambda x: pill(x, 24, "Active", GREEN, color=GREEN)]
    else:
        chips = [lambda x: pill(x, 24, "Stable", FAINT)]
    if p["release"]:
        chips.append(lambda x: pill(x, 24, p["release"]))
    chips.append(lambda x: star_pill(x, 24, p["stars"]))
    x = rx
    for chip in chips:
        s, w = chip(x)
        o.append(s)
        x += w + 8

    # Language band: ticks shaded by each language's share of the code.
    langs = p["languages"][:2]
    rest = 1 - sum(share for _, share in langs)
    if rest > 0.005:
        langs.append(("Other", rest))
    langs = langs or [("No code", 1.0)]
    shades = [(ACCENT, 1), ("#fff", .7), ("#fff", .28)]
    by, bh = 84, 26
    o.append(f'<rect x="{rx}" y="{by}" width="{W-36-rx}" height="{bh}" rx="{bh/2}" fill="#fff" fill-opacity=".03" '
             'stroke="#fff" stroke-opacity=".06"/>')
    x = rx
    for i, (name, share) in enumerate(langs):
        color, op = shades[min(i, 2)]
        label = f"{name} {share*100:.0f}%"
        o.append(f'<rect x="{x}" y="{by-17}" width="7" height="7" rx="1.5" fill="{color}" fill-opacity="{op}"/>')
        o.append(t(x + 12, by - 10, label, 11.5, DIM))
        x += 12 + text_width(label, 11.5) + 16
    ticks, tx0, tx1 = 36, rx + 16, W - 52
    seed = sum(map(ord, p["name"]))
    bounds, acc = [], 0.0
    for _, share in langs:
        acc += share
        bounds.append(acc)
    for i in range(ticks):
        pos = (i + .5) / ticks
        li = next((k for k, b in enumerate(bounds) if pos <= b + 1e-9), len(bounds) - 1)
        color, op = shades[min(li, 2)]
        h = 5 + 11 * abs(math.sin(i * 0.9 + seed) * math.cos(i * 0.37 + seed * .5))
        x = tx0 + i * (tx1 - tx0) / (ticks - 1)
        o.append(f'<line x1="{x:.1f}" y1="{by+bh/2-h/2:.1f}" x2="{x:.1f}" y2="{by+bh/2+h/2:.1f}" stroke="{color}" '
                 f'stroke-opacity="{op}" stroke-width="2" stroke-linecap="round"/>')
    o.append("</svg>")
    return "\n".join(o)


SNAKE_PAUSE = 0.0    # seconds the snake rests after its last bite (0 = keeps moving)
SNAKE_REFILL = 0.85  # share of the return trip over which the cells pop back, left to right
_KEYFRAMES = re.compile(r"@keyframes ([\w-]+)\{((?:[^{}]*\{[^}]*\})*)\}")


def _frames(body):
    """'2%,5%{a}9%{b}' -> sorted [(2.0, 'a'), (5.0, 'a'), (9.0, 'b')]."""
    out = []
    for sel, decl in re.findall(r"([^{}]+)\{([^}]*)\}", body):
        out += [(float(p.strip().rstrip("%")), decl) for p in sel.split(",")]
    return sorted(out)


def _emit(name, frames):
    return f"@keyframes {name}{{" + "".join(f"{p:.3f}%{{{d}}}" for p, d in frames) + "}"


def retime_snake(css, snake_svg):
    """Change snk's loop so that once everything is eaten (after an optional SNAKE_PAUSE), the cells pop back
    in a wave from left to right while it heads back to the start.

    snk's loop: cells are eaten up to time E (percent of the loop), the snake spends the rest
    of the loop travelling back, then everything snaps back at once when the loop restarts.
    """
    dur = int(re.search(r"(\d+)ms", css).group(1))
    kfs = {m.group(1): m.group(2) for m in _KEYFRAMES.finditer(css)}
    cells = {n: _frames(b) for n, b in kfs.items() if n.startswith("c")}
    if not cells:
        return css
    eat_end = max(f[0][0] for f in cells.values())

    t_eat, t_back = dur * eat_end / 100, dur * (100 - eat_end) / 100
    new_dur = t_eat + SNAKE_PAUSE * 1000 + t_back
    a, b = 100 * t_eat / new_dur, 100 * (t_eat + SNAKE_PAUSE * 1000) / new_dur

    def remap(p):
        return a * p / eat_end if p <= eat_end else b + (100 - b) * (p - eat_end) / (100 - eat_end)

    # Column of each cell class, for the refill wave.
    cols = {}
    for cls, x in re.findall(r'<rect class="c (c\w+)" x="([\d.]+)"', snake_svg):
        cols[cls] = float(x)
    left, right = min(cols.values()), max(cols.values())

    out = []
    for name, frames in cells.items():
        eaten, full = frames[0][0], frames[0][1]          # 'fill:var(--cN)' until eaten
        empty = next(d for p, d in frames if p > eaten)    # 'fill:var(--ce)'
        r = b + (100 - b) * SNAKE_REFILL * (cols.get(name, left) - left) / max(1, right - left)
        base = "translate(-1px,-1px)"
        # Every frame sets transform, so the size doesn't drift between frames.
        out.append(_emit(name, [
            (0, f"{full};transform:{base}"), (remap(eaten), f"{full};transform:{base}"),
            (remap(eaten) + .02, f"{empty};transform:{base}"), (r - .02, f"{empty};transform:{base}"),
            (r, f"{empty};transform:{base} scale(.2)"),
            (r + .02, f"{full};transform:{base} scale(.2)"), (min(r + 1.2, 99.9), f"{full};transform:{base} scale(1.18)"),
            (min(r + 2.4, 99.95), f"{full};transform:{base}"), (100, f"{full};transform:{base}")]))

    # Snake: after the last bite, head back along the empty lane above the
    # grid (instead of snk's route through the top row) so it never crosses the returning cells.
    def xy(decl):
        v = [float(n) for n in re.findall(r"(-?[\d.]+)px", decl)]
        return v[0], v[1]

    segs = sorted((n for n in kfs if re.fullmatch(r"s\d+", n)), key=lambda n: int(n[1:]))
    seg_frames = {n: _frames(kfs[n]) for n in segs}
    at_rest, home = {}, {}
    for n in segs:
        frames = seg_frames[n]
        p0, d0 = max((f for f in frames if f[0] <= eat_end), key=lambda f: f[0])
        p1, d1 = min((f for f in frames if f[0] >= eat_end), key=lambda f: f[0])
        (x0, y0), (x1, y1) = xy(d0), xy(d1)
        u = 0 if p1 == p0 else (eat_end - p0) / (p1 - p0)
        at_rest[n] = (x0 + (x1 - x0) * u, y0 + (y1 - y0) * u)
        home[n] = xy(frames[0][1])  # where it sits at the start of the loop
    if segs:
        lane = home[segs[0]][1]  # the row above the grid where the snake waits
        hx = at_rest[segs[0]][0]
        travel_end = 100  # arrive home exactly as the loop restarts, so there is no stall
        for i, n in enumerate(segs):
            # Each segment follows the ones ahead of it, then the head's route up and left.
            path = [at_rest[n]] + [at_rest[m] for m in reversed(segs[:i])] + [(hx, lane), home[n]]
            pts = [path[0]] + [p for j, p in enumerate(path[1:], 1) if math.dist(p, path[j - 1]) > .01]
            lengths = [0.0]
            for j in range(1, len(pts)):
                lengths.append(lengths[-1] + math.dist(pts[j - 1], pts[j]))
            total = lengths[-1] or 1
            frames = [(remap(p), d) for p, d in seg_frames[n] if p <= eat_end]
            frames.append((a, f"transform:translate({pts[0][0]:.1f}px,{pts[0][1]:.1f}px)"))
            for (px, py), ln in zip(pts, lengths):
                frames.append((b + (travel_end - b) * ln / total, f"transform:translate({px:.1f}px,{py:.1f}px)"))
            frames.append((100, f"transform:translate({pts[-1][0]:.1f}px,{pts[-1][1]:.1f}px)"))
            out.append(_emit(n, sorted(frames, key=lambda f: f[0])))

    for name, body in kfs.items():
        frames = _frames(body)
        if name.startswith("u"):
            # Progress bar: fill as before, then drain while the cells come back.
            filled = [(remap(p), d) for p, d in frames if p <= eat_end]
            last = max(frames)[1]
            out.append(_emit(name, filled + [(b, last), (b + (100 - b) * SNAKE_REFILL, "transform:scale(0,1)"),
                                             (100, "transform:scale(0,1)")]))

    css = _KEYFRAMES.sub("", css).replace(f"{dur}ms", f"{round(new_dur)}ms")
    return css + "".join(out)


def snake_panel(snake_svg):
    """Frame the Platane/snk SVG as a panel by drawing the panel into the snake's own SVG.

    (Embedding it as an <image> would stop its animation.) Cells are enlarged from 12 to 14
    units, keeping their 16-unit spacing, and the grid spans nearly the full panel width.
    """
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', snake_svg).group(1).split()]
    pad, head, foot = 18, 62, 14
    x0, y0 = vb[0] - pad, vb[1] - head
    w, h = vb[2] + 2 * pad, vb[3] + head + foot
    k = w / 1000  # keep text and corners the same size as on the 1000-wide panels

    root = ":root{" + ";".join(f"{n}:{v}" for n, v in SNAKE_COLORS.items()) + "}"
    css = re.sub(r":root\{[^}]*\}", root, re.search(r"<style>(.*?)</style>", snake_svg, re.S).group(1), count=1)
    css = css.replace("width:12px;height:12px", "width:14px;height:14px;transform:translate(-1px,-1px);"
                                                "transform-box:fill-box;transform-origin:center")
    try:
        css = retime_snake(css, snake_svg)
    except Exception as exc:  # unexpected snk output: keep its own timeline
        print(f"snake retime skipped: {exc}")
    css += f"{FONT_CSS}text{{font-family:{FONT_STACK}}}"

    body = snake_svg[snake_svg.index("</style>") + len("</style>"):snake_svg.rindex("</svg>")]
    r = 22 * k
    panel = (
        "<defs>"
        f'<linearGradient id="pbg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG_A}"/>'
        f'<stop offset="1" stop-color="{BG_B}"/></linearGradient>'
        '<radialGradient id="pglow" cx="0.5" cy="0" r="0.9"><stop offset="0" stop-color="#ffffff" stop-opacity=".07"/>'
        '<stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient></defs>'
        f'<rect x="{x0+.5:.1f}" y="{y0+.5:.1f}" width="{w-1:.1f}" height="{h-1:.1f}" rx="{r:.1f}" fill="url(#pbg)"/>'
        f'<rect x="{x0+.5:.1f}" y="{y0+.5:.1f}" width="{w-1:.1f}" height="{h-1:.1f}" rx="{r:.1f}" fill="url(#pglow)" '
        'stroke="#fff" stroke-opacity=".08"/>'
        + corner_cubes(w, h, x0, y0, k, seed="snake") +
        f'<text x="{x0 + 40*k:.1f}" y="{y0 + 50*k:.1f}" font-size="{11.5*k:.2f}" font-weight="400" fill="{FAINT}" '
        'letter-spacing="2">CONTRIBUTION GRAPH</text>'
        f'<text x="{x0 + w - 40*k:.1f}" y="{y0 + 50*k:.1f}" font-size="{11.5*k:.2f}" font-weight="400" fill="{FAINT}" '
        'letter-spacing="2" text-anchor="end">LAST 12 MONTHS</text>'
    )
    return (f'<svg viewBox="{x0:g} {y0:g} {w:g} {h:g}" width="{w:g}" height="{h:g}" '
            'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Contribution graph eaten by a snake">'
            f"<style>{css}</style>{panel}{body}</svg>")


def update_readme(path, user, repos):
    """Point the project row links at the repos drawn this run."""
    raw = f"https://raw.githubusercontent.com/{user}/{user}/output"
    rows = "\n".join(
        f'<a href="{escape(r["url"])}"><picture>'
        f'<source media="(prefers-color-scheme: light)" srcset="{raw}/project-{i}-light.svg" />'
        f'<img alt="{escape(r["name"])}: {escape(r["description"])}" width="100%" src="{raw}/project-{i}.svg" />'
        f'</picture></a>' for i, r in enumerate(repos, 1))
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = re.sub(r"(<!-- projects:start -->).*?(<!-- projects:end -->)",
                 lambda m: f"{m.group(1)}\n{rows}\n{m.group(2)}", text, flags=re.S)
    if new != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        print("README project links updated")


# ---------------------------------------------------------------- main

def main():
    global TOKEN, FONT_CSS
    args = sys.argv[1:]
    opts = {}
    for flag in ("--snake", "--readme"):
        if flag in args:
            i = args.index(flag)
            opts[flag] = args[i + 1]
            del args[i:i + 2]
    user, out_dir = args
    TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not TOKEN:
        raise SystemExit("Set GITHUB_TOKEN")
    os.makedirs(out_dir, exist_ok=True)
    FONT_CSS = load_font(os.environ.get("FONT_CACHE", os.path.join(tempfile.gettempdir(), "profile-fonts")))

    s = fetch_contributions(user)
    print(json.dumps({k: v for k, v in s.items() if k in
                      ("last_year", "private", "all_time", "current_streak", "longest_streak", "this_week",
                       "repos", "stars")}))
    repos = fetch_repos(user)
    snake = None
    if "--snake" in opts:
        with open(opts["--snake"], encoding="utf-8") as f:
            snake = f.read()

    files = {}
    for theme, suffix in (("dark", ""), ("light", "-light")):
        set_theme(theme)
        panels = {
            "hero": hero(sorted(repos, key=lambda r: r["committed"], reverse=True)),
            "activity": activity(s),
            "languages": languages(s),
        }
        if snake:
            panels["snake"] = snake_panel(snake)
        for i, p in enumerate(repos, 1):
            panels[f"project-{i}"] = project_row(p, s["today"])
        files.update({f"{name}{suffix}.svg": themed(svg) for name, svg in panels.items()})
    set_theme("dark")
    for name, svg in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(svg)
    print("wrote", ", ".join(files))
    if "--readme" in opts:
        update_readme(opts["--readme"], user, repos)


if __name__ == "__main__":
    main()
