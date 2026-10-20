"""Daily repository statistics, kept on the `stats` branch: release downloads, clones, page views,
referrers, stars and forks, with charts of them over time.

GitHub shows clones and page views for the last 14 days only, and release downloads as running totals.
This script, run once a day by .github/workflows/stats.yml, adds the latest numbers to CSV files on the
`stats` branch, so the history reaches back further than 14 days. It also rewrites a summary there
(README.md), two badge files that shields.io reads for the badges in the main README, and the charts
that both READMEs show (charts/activity-light.svg and charts/activity-dark.svg, for GitHub's light and
dark themes).

Stars and forks have a history on GitHub already: the lists of stargazers and forks give the day of each
star and fork, a hundred to a request. stars.csv is rebuilt from them on every run, so it reaches back to
the first star.

It talks to GitHub only through the REST API, never with git: GitHub counts every clone, including a
checkout by a build machine, so a daily checkout would show up as a clone of its own.

Tokens, from the environment:
  GITHUB_TOKEN  reads the releases, the repository, its stargazers and forks, and writes the stats branch
                (contents: write)
  STATS_TOKEN   reads clones, views and referrers, which need Administration read access: a fine-grained
                token for this repository. If it is missing, GITHUB_TOKEN is tried; when neither can read
                them, the traffic files are left as they were and the summary says when they were last read.

To look at the result without changing the branch, write the files into a folder instead:
    GITHUB_TOKEN=$(gh auth token) python3 .github/scripts/repo_stats.py --repo OWNER/NAME --out stats-preview
"""
import argparse
import csv
import io
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape, quoteattr

API = "https://api.github.com"
BRANCH = "stats"
COLOUR = "0b7f7d"  # the README's other badges
FIELDS = {
    "clones.csv": ["date", "clones", "unique"],
    "views.csv": ["date", "views", "unique"],
    "downloads.csv": ["date", "release", "asset", "downloads"],
    "referrers.csv": ["date", "referrer", "views", "unique"],
    "repo.csv": ["date", "stars", "forks", "watchers"],
    "stars.csv": ["date", "stars", "forks"],
}
CHARTS = {"light": "charts/activity-light.svg", "dark": "charts/activity-dark.svg"}
FILES = [*FIELDS, "state.json", "README.md", "badges/downloads.json", "badges/clones.json", *CHARTS.values()]


# ---- pure part: old files + fetched numbers -> new files -------------------------------------------

def read_csv(text):
    return list(csv.DictReader(io.StringIO(text or "")))


def write_csv(rows, fields):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows({k: r[k] for k in fields} for r in rows)
    return out.getvalue()


def merge_daily(rows, items, name):
    """Daily traffic rows ({date, name, unique}) updated with GitHub's buckets for the last 14 days
    ({timestamp, count, uniques}); a day GitHub reports again (today, still counting) is replaced."""
    by_date = {r["date"]: r for r in rows}
    for it in items:
        day = it["timestamp"][:10]
        by_date[day] = {"date": day, name: str(it["count"]), "unique": str(it["uniques"])}
    return [by_date[d] for d in sorted(by_date)]


def replace_day(rows, today, new):
    """A snapshot table with today's rows replaced by `new` (each given today's date)."""
    return [r for r in rows if r["date"] != today] + [{"date": today, **n} for n in new]


def star_history(stargazers, forks):
    """stars.csv rows from GitHub's lists ({starred_at} and {created_at}): for each day on which one of
    today's stargazers starred or a fork was made, the running totals at the end of that day."""
    stars = Counter(s["starred_at"][:10] for s in stargazers if s.get("starred_at"))
    made = Counter(f["created_at"][:10] for f in forks if f.get("created_at"))
    rows, s, f = [], 0, 0
    for day in sorted(stars.keys() | made.keys()):
        s, f = s + stars[day], f + made[day]
        rows.append({"date": day, "stars": str(s), "forks": str(f)})
    return rows


def is_app(asset):
    """Downloads of the app itself, not of the checksum list."""
    return not asset.lower().endswith(".txt")


def total(rows, key):
    return sum(int(r[key]) for r in rows)


def badge(label, n):
    return json.dumps({"schemaVersion": 1, "label": label, "message": f"{n:,}", "color": COLOUR}) + "\n"


def summary(tables, repo, today, traffic_read):
    clones, views = tables["clones.csv"], tables["views.csv"]
    latest = lambda rows: [r for r in rows if r["date"] == max((x["date"] for x in rows), default="")]
    downloads, referrers, info = latest(tables["downloads.csv"]), latest(tables["referrers.csv"]), latest(tables["repo.csv"])
    since = min((r["date"] for r in clones + views), default=None)
    app = [r for r in downloads if is_app(r["asset"])]
    lines = [f"# {repo.split('/')[-1].capitalize()} statistics", "",
             f"Updated on {today} by the Repository stats workflow on main (`.github/workflows/stats.yml`), from "
             "GitHub's API. GitHub itself only shows the last 14 days of clones and views; this branch keeps every day "
             + (f"since {since}." if since else "from the first run on."), ""]
    if traffic_read != today:
        lines += [f"Clones, views and referrers could not be read today (last read: {traffic_read or 'never'}). "
                  "The STATS_TOKEN secret is missing or has expired.", ""]
    # Each chart in a link whose address ends in GitHub's marker for its theme, so GitHub shows the one for
    # the theme chosen on GitHub. A <picture> would follow the system's light or dark setting instead.
    lines += [f'<a href="{path}#gh-{theme}-mode-only"><img src="{path}" width="800" alt="Charts of the downloads '
              'of the app, the stars and forks over time, and the clones and page views per day"></a>'
              for theme, path in CHARTS.items()] + [""]
    lines += ["## Totals", "", "| | |", "|---|---|",
              f"| Downloads of the app, all releases | {total(app, 'downloads'):,} |"]
    if since:
        lines += [f"| Clones since {since} | {total(clones, 'clones'):,} ({total(clones, 'unique'):,} unique per day, added up) |",
                  f"| Page views since {since} | {total(views, 'views'):,} ({total(views, 'unique'):,} unique per day, added up) |"]
    if info:
        i = info[0]
        lines.append(f"| Stars, forks, watchers | {int(i['stars']):,}, {int(i['forks']):,}, {int(i['watchers']):,} |")
    if downloads:
        lines += ["", "## Downloads by release", "", "| Release | File | Downloads |", "|---|---|---|"]
        lines += [f"| {r['release']} | {r['asset']} | {int(r['downloads']):,} |" for r in downloads]
    days = sorted({r["date"] for r in clones + views}, reverse=True)[:30]
    if days:
        c, v = {r["date"]: r for r in clones}, {r["date"]: r for r in views}
        cell = lambda t, d, k: t[d][k] if d in t else "0"
        lines += ["", "## Last 30 days", "", "| Day | Clones | Unique cloners | Views | Unique visitors |", "|---|---|---|---|---|"]
        lines += [f"| {d} | {cell(c, d, 'clones')} | {cell(c, d, 'unique')} | {cell(v, d, 'views')} | {cell(v, d, 'unique')} |"
                  for d in days]
    if referrers:
        lines += ["", f"## Where visitors came from (the 14 days to {referrers[0]['date']})", "",
                  "| Site | Views | Unique visitors |", "|---|---|---|"]
        lines += [f"| {r['referrer']} | {r['views']} | {r['unique']} |" for r in referrers]
    lines += ["", "## Reading the numbers", "",
              "A unique cloner or visitor is counted once a day, so someone who clones on three days counts three "
              "times in the added-up uniques.",
              "",
              "Clones include those by build machines: every run of the Desktop build workflow clones the repository. "
              "The stats workflow does not, so it adds nothing to the counts.",
              "",
              "Stars and forks come from GitHub's lists of the current stargazers and forks, which give the day of "
              "each star and fork, so their history reaches back to the first star. A star that is taken back "
              "leaves the history altogether. stars.csv holds the running totals for each day on which they grew.",
              "",
              "The CSV files beside this summary hold every number by day. The download, referrer and repository "
              "tables are snapshots: one set of rows per day, with GitHub's running totals at the time. The charts "
              "are drawn from these files on every run, in a light and a dark version (charts/).", ""]
    return "\n".join(lines)


def update(old, fetched, repo, today):
    """New file contents from the old ones (path -> text, missing files absent) and today's numbers:
    fetched = {"releases": [...], "repo": {...}, "stargazers": [...] or None, "forks": ...,
    "clones": [...] or None, "views": ..., "referrers": ...} (None when they could not be read)."""
    tables = {name: read_csv(old.get(name)) for name in FIELDS}
    state = json.loads(old.get("state.json") or "{}")
    if fetched["clones"] is not None:
        tables["clones.csv"] = merge_daily(tables["clones.csv"], fetched["clones"], "clones")
        tables["views.csv"] = merge_daily(tables["views.csv"], fetched["views"], "views")
        tables["referrers.csv"] = replace_day(tables["referrers.csv"], today, [
            {"referrer": r["referrer"], "views": str(r["count"]), "unique": str(r["uniques"])} for r in fetched["referrers"]])
        state["traffic_read"] = today
    if fetched.get("stargazers") is not None and fetched.get("forks") is not None:
        tables["stars.csv"] = star_history(fetched["stargazers"], fetched["forks"])
    releases = [rel for rel in fetched["releases"] if not rel.get("draft")]
    tables["downloads.csv"] = replace_day(tables["downloads.csv"], today, [
        {"release": rel["tag_name"], "asset": a["name"], "downloads": str(a["download_count"])}
        for rel in releases for a in rel["assets"]])
    r = fetched["repo"]
    tables["repo.csv"] = replace_day(tables["repo.csv"], today, [
        {"stars": str(r["stargazers_count"]), "forks": str(r["forks_count"]), "watchers": str(r["subscribers_count"])}])
    new = {name: write_csv(rows, FIELDS[name]) for name, rows in tables.items()}
    new["state.json"] = json.dumps(state, indent=1, sort_keys=True) + "\n"
    new["README.md"] = summary(tables, repo, today, state.get("traffic_read"))
    latest = [x for x in tables["downloads.csv"] if x["date"] == today]
    new["badges/downloads.json"] = badge("downloads", total([x for x in latest if is_app(x["asset"])], "downloads"))
    new["badges/clones.json"] = badge("clones", total(tables["clones.csv"], "clones"))
    published = [(rel["published_at"][:10], rel["tag_name"]) for rel in releases if rel.get("published_at")]
    data = chart_data(tables, published, today)
    for theme, path in CHARTS.items():
        new[path] = activity_chart(data, repo, THEMES[theme])
    return new


# ---- charts: four panels on one time axis, as SVG written by hand ---------------------------------
#
# Downloads and stars are running totals, drawn as lines from the day their history starts; clones and
# page views are the daily numbers, drawn as columns with the unique count in front. The panels share the
# days, so a release lines up with what followed it. Light and dark differ only in their colours, and
# each is drawn on a card of its own background, so it still reads on a page of the other theme (where
# GitHub's theme markers don't apply, both show). Each mark carries its numbers in data- attributes and
# each panel its scales (data-x, data-y), which tests/test_repo_stats.py checks.

# GitHub's text and border greys and page colours, the README's teal as the accent (the light teal holds
# as much chroma as sRGB allows at its lightness), and an orange for forks that stays apart from it with
# colour blindness too (checked as a pair for each theme).
THEMES = {
    "light": {"ink": "#1f2328", "text": "#59636e", "grid": "#e4e8ec", "base": "#b8c0c8", "release": "#8c959f",
              "accent": "#0b7f7d", "pale": "#9dcccb", "forks": "#eb6834", "card": "#ffffff", "edge": "#d1d9e0"},
    "dark": {"ink": "#e6edf3", "text": "#9198a1", "grid": "#2d333b", "base": "#3d444d", "release": "#656c76",
             "accent": "#14a3a1", "pale": "#176a69", "forks": "#d95926", "card": "#0d1117", "edge": "#3d444d"},
}
FONT = 'system-ui, -apple-system, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
WIDTH, MARGIN, GUTTER = 800, 20, 40  # MARGIN: the room inside the card's edge, on every side
PANEL_W = (WIDTH - 2 * MARGIN - GUTTER) / 2
# The plot inside a panel: room on the left for the value axis, on the right for the last value, above
# for the title, subtitle and legend, below for the dates
PLOT_LEFT, PLOT_RIGHT, PLOT_TOP, PLOT_H = 34, 30, 62, 120
PANEL_H = PLOT_TOP + PLOT_H + 24
MONTHS = "January February March April May June July August September October November December".split()


def short_date(d, year=False):
    return f"{d.day} {MONTHS[d.month - 1][:3]}" + (f" {d.year}" if year else "")


def long_date(d):
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def period(first, last):
    """'26 Sep to 20 Oct 2026', with both years when they differ."""
    if first == last:
        return short_date(last, True)
    return f"{short_date(first, first.year != last.year)} to {short_date(last, True)}"


def compact(n):
    """A number in little room: 950, 1K, 1.5K, 12.3K, 2M."""
    for size, unit in ((1_000_000, "M"), (1_000, "K")):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + unit
    return str(n)


def shown(n):
    """A count as written beside a mark: 1,234 up to 9,999, then 12.3K."""
    return f"{n:,}" if n < 10_000 else compact(n)


def counted(n, noun):
    return f"{shown(n)} {noun}{'' if n == 1 else 's'}"


def axis(vmax):
    """The top of a value axis from 0 that leaves room above vmax for a label, and the step between its
    gridlines: the smallest of 1, 2 or 5 times a power of ten that needs at most five of them."""
    want = max(vmax * 1.1, 1)
    power = 1
    while True:
        for step in (power, 2 * power, 5 * power):
            top = math.ceil(want / step) * step
            if top // step <= 5:
                return top, step
        power *= 10


def num(v):
    """A coordinate: at most two decimals, no trailing zeros."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def attrs(**kw):
    """XML attributes; a trailing _ is dropped and other _ become -. None leaves one out."""
    return "".join(f" {k.rstrip('_').replace('_', '-')}={quoteattr(v if isinstance(v, str) else num(v))}"
                   for k, v in kw.items() if v is not None)


def text(x, y, s, size, fill, **kw):
    return f"<text{attrs(x=x, y=y, font_size=size, fill=fill, **kw)}>{escape(s)}</text>"


def line(x1, y1, x2, y2, stroke, **kw):
    return f"<line{attrs(x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, **kw)}/>"


def text_width(s, size):
    """Roughly how wide s is at size px in the fonts of FONT (to space labels, not to fit them tightly)."""
    widths = {**dict.fromkeys("ijlI.,:;'!|", 0.28), **dict.fromkeys("frt()[]- ", 0.36),
              **dict.fromkeys("mwMW%", 0.86)}
    return size * sum(widths.get(c, 0.66 if c.isupper() else 0.56) for c in s)


def wrap(s, size, width):
    """Lines of at most `width` px."""
    lines = [""]
    for word in s.split():
        joined = f"{lines[-1]} {word}".strip()
        if lines[-1] and text_width(joined, size) > width:
            lines.append(word)
        else:
            lines[-1] = joined
    return lines


class Days:
    """Maps each day from `first` to `last` onto the centre of its slot between the pixels x0 and x1."""

    def __init__(self, first, last, x0, x1):
        self.first, self.last = first, last
        self.slot = (x1 - x0) / ((last - first).days + 1)
        self.x0 = x0 + self.slot / 2

    def __call__(self, d):
        return self.x0 + (d - self.first).days * self.slot

    def ticks(self, room=60):
        """The days to label, with their labels: every day or second day, every Monday or second Monday,
        the first of every 1, 2, 3, 6 or 12 months, or of every few years, whichever is the most often
        that leaves `room` px from one label to the next."""
        days = [self.first + timedelta(i) for i in range((self.last - self.first).days + 1)]
        mondays = [d for d in days if d.weekday() == 0]
        for step, picked in ((1, days), (2, days[::2]), (7, mondays), (14, mondays[::2])):
            if step * self.slot >= room:
                return [(d, short_date(d)) for d in picked]
        firsts = [d for d in days if d.day == 1]
        for months in (1, 2, 3, 6, 12):
            if 28 * months * self.slot >= room:
                return [(d, MONTHS[d.month - 1][:3] + (f" {d.year}" if d.month == 1 else ""))
                        for d in firsts if (d.month - 1) % months == 0]
        years = math.ceil(room / (365 * self.slot))
        return [(d, str(d.year)) for d in firsts if d.month == 1 and d.year % years == 0]


def corners(points):
    """The points a line needs: its ends and wherever the value changes (the days between lie on it)."""
    return [(d, v) for i, (d, v) in enumerate(points)
            if i in (0, len(points) - 1) or points[i - 1][1] != v or points[i + 1][1] != v]


def daily(rows, key, last):
    """A running total for each day from its first day above zero to `last`, from rows {date, key} for the
    days on which it changed."""
    changes = sorted((date.fromisoformat(r["date"]), int(r[key])) for r in rows)
    first = next((d for d, v in changes if v > 0), None)
    out, value, i = [], 0, 0
    d = first
    while d is not None and d <= last:
        while i < len(changes) and changes[i][0] <= d:
            value, i = changes[i][1], i + 1
        out.append((d, value))
        d += timedelta(1)
    return out


def chart_data(tables, releases, today):
    """The numbers the charts draw, by day: the running total of app downloads (from the daily snapshots),
    stars and forks (each from its first day), clones and views with their uniques, and the releases
    [(day, tag)]; first and last are the days the time axis spans."""
    by_day = {}
    for r in tables["downloads.csv"]:
        by_day.setdefault(r["date"], 0)
        if is_app(r["asset"]):
            by_day[r["date"]] += int(r["downloads"])
    last = date.fromisoformat(today)
    traffic = lambda name, key: [(date.fromisoformat(r["date"]), int(r[key]), int(r["unique"])) for r in tables[name]]
    data = {"downloads": corners(sorted((date.fromisoformat(d), n) for d, n in by_day.items())),
            "stars": corners(daily(tables["stars.csv"], "stars", last)),
            "forks": corners(daily(tables["stars.csv"], "forks", last)),
            "clones": traffic("clones.csv", "clones"), "views": traffic("views.csv", "views"),
            "releases": sorted((date.fromisoformat(d), tag) for d, tag in releases)}
    days = [p[0] for name in ("downloads", "stars", "forks", "clones", "views", "releases") for p in data[name]]
    data["first"], data["last"] = min(days, default=last), max(days + [last])
    return data


def describe(data):
    """The charts in words, for the SVG's <desc>."""
    out = [f"Four charts on one time axis, from {long_date(data['first'])} to {long_date(data['last'])}."]
    dl = data["downloads"]
    out.append(f"Downloads of the app, all releases, as a running total: {shown(dl[-1][1])} on {long_date(dl[-1][0])}, "
               f"recorded since {long_date(dl[0][0])}." if dl else "Downloads of the app: none recorded yet.")
    if data["releases"]:
        out.append("Releases: " + ", ".join(f"{tag} on {long_date(d)}" for d, tag in data["releases"]) + ".")
    for name in ("stars", "forks"):
        s = data[name]
        out.append(f"{name.capitalize()}: {shown(s[-1][1])}, the first on {long_date(s[0][0])}." if s else
                   f"{name.capitalize()}: none yet.")
    for name, what in (("clones", "Clones"), ("views", "Page views")):
        rows = data[name]
        if any(n for _, n, _ in rows):
            d, n, _ = max(rows, key=lambda r: (r[1], r[0]))
            out.append(f"{what} per day: {shown(sum(r[1] for r in rows))} since {long_date(rows[0][0])}, "
                       f"the most {shown(n)} on {long_date(d)}.")
        else:
            out.append(f"{what} per day: none recorded yet.")
    return " ".join(out)


class Panel:
    """One chart: its title, subtitle, value axis, gridlines and dates, at (x, y) in the picture."""

    def __init__(self, x, y, name, title, subtitle, vmax, data, t):
        self.x, self.y, self.t = x, y, t
        self.x0, self.x1 = x + PLOT_LEFT, x + PANEL_W - PLOT_RIGHT
        self.base, self.top_y = y + PLOT_TOP + PLOT_H, y + PLOT_TOP
        self.top, step = axis(vmax)
        self.days = d = Days(data["first"], data["last"], self.x0, self.x1)
        scales = attrs(data_panel=name, data_x=f"{d.first} {d.last} {num(d.x0)} {num(d.slot)}",
                       data_y=f"{self.top} {num(self.base)} {num(self.top_y)}")
        self.out = [f"<g{scales}>", text(x, y + 14, title, 13, t["ink"], font_weight="600"),
                    text(x, y + 32, subtitle, 12, t["text"])]
        for v in range(step, self.top + 1, step):
            self.out.append(line(self.x0, self.y_of(v), self.x1, self.y_of(v), t["grid"]))
            self.out.append(text(self.x0 - 6, self.y_of(v) + 4, compact(v), 11, t["text"], text_anchor="end"))
        self.out.append(line(self.x0, self.base, self.x1, self.base, t["base"]))
        self.out.append(text(self.x0 - 6, self.base + 4, "0", 11, t["text"], text_anchor="end"))
        right = x + PANEL_W
        for day, label in d.ticks():
            w = text_width(label, 11) / 2
            cx = min(max(d(day), x + w), right - w)
            self.out.append(text(cx, self.base + 16, label, 11, t["text"], text_anchor="middle"))

    def y_of(self, v):
        return self.base - v * PLOT_H / self.top

    def legend(self, items):
        """Keys left to right in the row above the plot: (mark, colour, label), mark "line" or "box",
        or None for a label alone."""
        x, y = self.x, self.y + 50
        for mark, colour, label in items:
            if mark == "line":
                self.out.append(line(x, y - 4, x + 14, y - 4, colour, stroke_width=2, stroke_linecap="round"))
                x += 20
            elif mark == "box":
                self.out.append(f"<rect{attrs(x=x, y=y - 10, width=10, height=10, rx=2, fill=colour)}/>")
                x += 16
            self.out.append(text(x, y, label, 12, self.t["text"]))
            x += text_width(label, 12) + 18

    def empty(self, message):
        self.out.append(text((self.x0 + self.x1) / 2, self.base - PLOT_H / 2 + 4, message, 12, self.t["text"],
                             text_anchor="middle", data_empty="1"))

    def running(self, name, points, colour, end_label=True, steps=False, note=None):
        """A running total as a line, a dot on its last day and, if end_label, its value beside the dot.

        A total that is known since it was 0 (steps: stars, forks) rises from the axis on its first day
        and steps up on each day it grew. One whose start wasn't seen (downloads, recorded daily from the
        first run) is a line between the days recorded, with a light wash under it, and `note` before its
        start says so where there is room. A single day is a dot with its value."""
        if not points:
            return
        xy = [(self.days(d), self.y_of(v)) for d, v in points]
        values = " ".join(f"{d}:{v}" for d, v in points)
        x, y = xy[0]
        if note and x - 8 - text_width(note, 11) >= self.x0 + 4:
            self.out.append(text(x - 8, y + 4, note, 11, self.t["text"], text_anchor="end", data_note=name))
        if steps:
            vertices = [(xy[0][0], self.base)]
            for i, (x, y) in enumerate(xy):
                if i:
                    vertices.append((x, xy[i - 1][1]))  # level until the day it grew
                vertices.append((x, y))
        else:
            vertices = xy
        numbers = {"data_series": name, "data_points": values}  # on the line, or on the dot if that's all
        single = len(vertices) == 1
        if not single:
            path = " ".join(f"{'L' if i else 'M'}{num(x)},{num(y)}" for i, (x, y) in enumerate(vertices))
            if not steps:
                wash = f"{path} L{num(xy[-1][0])},{num(self.base)} L{num(xy[0][0])},{num(self.base)} Z"
                self.out.append(f"<path{attrs(d=wash, fill=colour, fill_opacity=0.12)}/>")
            stroke = attrs(fill="none", stroke=colour, stroke_width=2, stroke_linejoin="round", stroke_linecap="round")
            self.out.append(f"<path{attrs(d=path)}{stroke}{attrs(**numbers)}/>")
        x, y = xy[-1]
        self.out.append(f"<circle{attrs(cx=x, cy=y, r=4, fill=colour, **(numbers if single else {}))}/>")
        if end_label or len(xy) == 1:
            self.out.append(text(x + 8, y + 4, shown(points[-1][1]), 12, self.t["ink"], font_weight="600",
                                 data_label=name))

    def columns(self, rows):
        """A column for each day: its full height the count, the part in front (darker) the uniques."""
        slot = self.days.slot
        w = min(24, slot * 0.62) if slot >= 5 else max(slot - 1, 0.6) if slot >= 2 else slot
        radius = min(4, w / 2) if w >= 4 else 0
        for d, n, unique in rows:
            if not n:
                continue
            x = self.days(d) - w / 2
            top, mid = self.y_of(n), self.y_of(unique)
            marks = []
            if unique < n:
                gap = 2 if mid - top >= 4 else 0
                bottom = self.base if not unique else mid - gap
                marks.append(column(x, w, top, bottom, radius, self.t["pale"]))
            if unique:
                marks.append(column(x, w, mid, self.base, radius if unique == n else 0, self.t["accent"]))
            self.out.append(f"<g{attrs(data_date=str(d), data_count=str(n), data_unique=str(unique))}>{''.join(marks)}</g>")
        if any(n for _, n, _ in rows):
            d, n, _ = max(rows, key=lambda row: (row[1], row[0]))
            x = min(max(self.days(d), self.x0), self.x1 + PLOT_RIGHT - text_width(shown(n), 11) / 2)
            self.out.append(text(x, self.y_of(n) - 6, shown(n), 11, self.t["ink"], text_anchor="middle",
                                 font_weight="600", data_label="most"))

    def releases(self, releases):
        """A hairline down the plot on the day of each release, its tag in the row above; a tag that would
        overlap a later one is left out (the line stays)."""
        placed = []
        for d, tag in sorted(releases, reverse=True):
            x = self.days(d)
            w = text_width(tag, 11)
            left = min(max(x - w / 2, self.x), self.x + PANEL_W - w)
            show = all(left + w + 6 <= a or left >= b + 6 for a, b in placed)
            marks = [line(x, self.y + 54 if show else self.top_y, x, self.base, self.t["release"])]
            if show:
                placed.append((left, left + w))
                marks.append(text(left, self.y + 50, tag, 11, self.t["text"]))
            self.out.append(f"<g{attrs(data_release=tag, data_date=str(d))}>{''.join(marks)}</g>")

    def svg(self):
        return self.out + ["</g>"]


def column(x, w, top, bottom, r, fill):
    """A column from bottom up to top, square at the bottom, its top corners rounded by r."""
    r = min(r, (bottom - top) / 2) if r else 0
    p = lambda a, b: f"{num(a)},{num(b)}"
    if r:
        d = (f"M{p(x, bottom)} L{p(x, top + r)} Q{p(x, top)} {p(x + r, top)} L{p(x + w - r, top)} "
             f"Q{p(x + w, top)} {p(x + w, top + r)} L{p(x + w, bottom)} Z")
    else:
        d = f"M{p(x, bottom)} L{p(x, top)} L{p(x + w, top)} L{p(x + w, bottom)} Z"
    return f"<path{attrs(d=d, fill=fill)}/>"


def activity_chart(data, repo, t):
    """The four panels as one SVG in the colours of theme t."""
    name = repo.split("/")[-1].capitalize()
    out = [text(MARGIN, MARGIN + 12, f"{name} on GitHub", 15, t["ink"], font_weight="600"),
           text(WIDTH - MARGIN, MARGIN + 12, period(data["first"], data["last"]), 12, t["text"], text_anchor="end")]
    left, right = MARGIN, MARGIN + PANEL_W + GUTTER
    row1 = MARGIN + 32
    row2 = row1 + PANEL_H + 22

    dl = data["downloads"]
    p = Panel(left, row1, "downloads", "Downloads of the app, all releases",
              "Running total of the installer and the Linux archive", max((v for _, v in dl), default=0), data, t)
    p.releases(data["releases"])
    p.running("downloads", dl, t["accent"],
              note=f"Recorded from {short_date(dl[0][0])}" if dl and dl[0][0] > data["first"] else None)
    if not dl:
        p.empty("No downloads recorded yet")
    out += p.svg()

    stars, forks = data["stars"], data["forks"]
    p = Panel(right, row1, "stars", "Stars and forks", "Running totals, from the day of each star and fork",
              max([v for _, v in stars + forks], default=0), data, t)
    if stars or forks:
        p.legend([("line", t["accent"], counted(stars[-1][1], "star")) if stars else (None, None, "No stars yet"),
                  ("line", t["forks"], counted(forks[-1][1], "fork")) if forks else (None, None, "No forks yet")])
    else:
        p.empty("No stars or forks yet")
    p.running("forks", forks, t["forks"], end_label=False, steps=True)
    p.running("stars", stars, t["accent"], end_label=False, steps=True)
    out += p.svg()

    for x, key, title, keys in ((left, "clones", "Clones per day", ("Clones", "Unique cloners")),
                                (right, "views", "Page views per day", ("Views", "Unique visitors"))):
        rows = data[key]
        since = short_date(rows[0][0], True) if rows else None
        p = Panel(x, row2, key, title, f"{shown(sum(r[1] for r in rows))} in all since {since}" if rows else
                  "Each day's count, from GitHub's traffic numbers", max((r[1] for r in rows), default=0), data, t)
        if any(r[1] for r in rows):
            p.legend([("box", t["pale"], keys[0]), ("box", t["accent"], keys[1])])
            p.columns(rows)
        else:
            p.empty(f"No {keys[0].lower()} since {since}" if rows else "Not recorded yet")
        out += p.svg()

    y = row2 + PANEL_H + 14
    for note in ("From GitHub's API, read every day by the Repository stats workflow. Clones include those by "
                 "build machines.", "Stars and forks are dated from GitHub's lists of today's stargazers and forks."):
        for s in wrap(note, 11, WIDTH - 2 * MARGIN):
            y += 15
            out.append(text(MARGIN, y, s, 11, t["text"]))
    height = math.ceil(y + MARGIN)
    return "\n".join([
        f"<svg{attrs(xmlns='http://www.w3.org/2000/svg', width=WIDTH, height=height)} viewBox=\"0 0 {WIDTH} {height}\""
        f" role=\"img\" aria-labelledby=\"title desc\" font-family={quoteattr(FONT)}>",
        f"<title id=\"title\">{escape(f'{name} on GitHub: downloads, stars and forks, clones and page views')}</title>",
        f"<desc id=\"desc\">{escape(describe(data))}</desc>",
        f"<rect{attrs(x=0.5, y=0.5, width=WIDTH - 1, height=height - 1, rx=6, fill=t['card'], stroke=t['edge'])}/>",
        *out, "</svg>", ""])


# ---- GitHub ---------------------------------------------------------------------------------------

class GitHubError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def call(token, path, method="GET", body=None, raw=False, accept=None):
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28",
                                          "Accept": accept or ("application/vnd.github.raw" if raw else "application/vnd.github+json"),
                                          "User-Agent": "repo-stats"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise GitHubError(e.code, e.read().decode(errors="replace")[:300]) from None
    return data.decode() if raw else json.loads(data or b"null")


def pages(token, path, accept=None):
    """Every item of a list, a hundred to a request."""
    items, page = [], 1
    while True:
        batch = call(token, f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}", accept=accept)
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def fetch(repo, token, traffic_token):
    info = call(token, f"/repos/{repo}")
    got = {"releases": pages(token, f"/repos/{repo}/releases"), "repo": info,
           "stargazers": None, "forks": None, "clones": None, "views": None, "referrers": None}
    try:  # the day of each star and fork; none to ask for while there are none
        got["stargazers"] = pages(token, f"/repos/{repo}/stargazers", accept="application/vnd.github.star+json") \
            if info.get("stargazers_count") else []
        got["forks"] = pages(token, f"/repos/{repo}/forks?sort=oldest") if info.get("forks_count") else []
    except GitHubError as e:
        got["stargazers"] = got["forks"] = None
        print(f"::warning::The stargazers and forks could not be read ({e}); stars.csv is left as it was.")
    for tok in dict.fromkeys(t for t in (traffic_token, token) if t):  # the stats token first, then the other
        try:
            got["clones"] = call(tok, f"/repos/{repo}/traffic/clones?per=day")["clones"]
            got["views"] = call(tok, f"/repos/{repo}/traffic/views?per=day")["views"]
            got["referrers"] = call(tok, f"/repos/{repo}/traffic/popular/referrers")
            break
        except GitHubError as e:
            if e.status not in (401, 403):
                raise
            got["clones"] = got["views"] = got["referrers"] = None
    if got["clones"] is None:
        print("::warning::Clones and views could not be read: set the STATS_TOKEN secret "
              "(a fine-grained token for this repository with Administration read access).")
    return got


def read_branch(repo, token, branch):
    """(commit sha or None, {path: text}) of the stats branch."""
    try:
        head = call(token, f"/repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]
    except GitHubError as e:
        if e.status == 404:
            return None, {}
        raise
    files = {}
    for path in FILES:
        try:
            files[path] = call(token, f"/repos/{repo}/contents/{path}?ref={head}", raw=True)
        except GitHubError as e:
            if e.status != 404:
                raise
    return head, files


def commit(repo, token, branch, head, files, message):
    """One commit with the given files on the branch (created, without history, if it doesn't exist)."""
    tree = {"tree": [{"path": p, "mode": "100644", "type": "blob", "content": t} for p, t in sorted(files.items())]}
    if head:
        tree["base_tree"] = call(token, f"/repos/{repo}/git/commits/{head}")["tree"]["sha"]
    sha = call(token, f"/repos/{repo}/git/trees", "POST", tree)["sha"]
    new = call(token, f"/repos/{repo}/git/commits", "POST", {"message": message, "tree": sha, "parents": [head] if head else []})["sha"]
    if head:
        call(token, f"/repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": new})
    else:
        call(token, f"/repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": new})
    return new


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/NAME")
    ap.add_argument("--branch", default=BRANCH)
    ap.add_argument("--out", help="write the files into this folder instead of committing them")
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not args.repo or not token:
        sys.exit("needs --repo (or GITHUB_REPOSITORY) and GITHUB_TOKEN")
    today = datetime.now(timezone.utc).date().isoformat()
    head, old = read_branch(args.repo, token, args.branch)
    new = update(old, fetch(args.repo, token, os.environ.get("STATS_TOKEN")), args.repo, today)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        # the job's summary has no branch to resolve the charts' paths against
        raw = f"https://raw.githubusercontent.com/{args.repo}/{args.branch}/"
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(re.sub(r'(src|href)="(charts/)', rf'\1="{raw}\2', new["README.md"]))
    changed = {p: t for p, t in new.items() if old.get(p) != t}
    if args.out:
        for p, t in new.items():
            os.makedirs(os.path.dirname(os.path.join(args.out, p)) or args.out, exist_ok=True)
            with open(os.path.join(args.out, p), "w", encoding="utf-8") as f:
                f.write(t)
        print(f"wrote {len(new)} files to {args.out} ({len(changed)} differ from the {args.branch} branch)")
    elif changed:
        sha = commit(args.repo, token, args.branch, head, changed, f"Stats for {today}")
        print(f"{args.branch}: {sha[:7]}, {len(changed)} files updated")
    else:
        print("nothing changed")


if __name__ == "__main__":
    main()
