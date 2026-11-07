"""Tests for the repository statistics (.github/scripts/repo_stats.py): the daily history grows past
GitHub's 14 days, a day reported again replaces the old numbers, snapshots keep one set of rows a day,
the checksum list isn't counted as a download, the badges and summary show the totals, and a run without
access to the traffic keeps the old traffic files and says so. The star history is rebuilt from the
stargazers and forks, which are read a hundred to a request. The charts are valid SVG whose marks sit where
their numbers say (the running total of app downloads, the releases, the stars and forks, the clones and
views per day), light and dark differ only in their colours, and a sparse or empty history still draws.
The workflow never clones the repository (a clone would count itself) and the script needs only the standard
library; the README's badges and charts read the files the script writes. Offline: the GitHub calls are
replaced.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("repo_stats", ROOT / ".github" / "scripts" / "repo_stats.py")
assert spec and spec.loader
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)

REPO = "owner/sedjem"
SVG = "{http://www.w3.org/2000/svg}"


def day(d, count, uniques):
    return {"timestamp": f"{d}T00:00:00Z", "count": count, "uniques": uniques}


def fetched(clones=(), views=(), downloads=(("v0.1.0", "Sedjem-setup.exe", 9), ("v0.1.0", "SHA256SUMS.txt", 1)),
            referrers=(("github.com", 6, 3),), traffic=True, published=None, stargazers=None, forks=None):
    """What fetch() returns. published: {tag: day} for the releases' published_at; stargazers and forks:
    the days of each star and fork (None: not read)."""
    releases = {}
    for tag, name, n in downloads:
        releases.setdefault(tag, []).append({"name": name, "download_count": n})
    return {"releases": [{"tag_name": t, "assets": a, **({"published_at": f"{published[t]}T15:25:45Z"} if published else {})}
                         for t, a in releases.items()],
            "repo": {"stargazers_count": 4, "forks_count": 1, "subscribers_count": 2},
            "stargazers": None if stargazers is None else [{"starred_at": f"{d}T16:33:08Z", "user": {"login": "x"}} for d in stargazers],
            "forks": None if forks is None else [{"created_at": f"{d}T09:00:00Z", "full_name": "x/sedjem"} for d in forks],
            "clones": list(clones) if traffic else None, "views": list(views) if traffic else None,
            "referrers": [{"referrer": r, "count": c, "uniques": u} for r, c, u in referrers] if traffic else None}


class Update(unittest.TestCase):
    def test_history_grows_past_fourteen_days_and_a_repeated_day_is_replaced(self):
        first = stats.update({}, fetched(clones=[day("2026-10-01", 2, 2), day("2026-10-02", 1, 1)]), REPO, "2026-10-02")
        # the next run: GitHub no longer reports 1 October, and 2 October has grown since
        second = stats.update(first, fetched(clones=[day("2026-10-02", 3, 2), day("2026-10-03", 5, 4)]), REPO, "2026-10-03")
        rows = stats.read_csv(second["clones.csv"])
        self.assertEqual([(r["date"], r["clones"], r["unique"]) for r in rows],
                         [("2026-10-01", "2", "2"), ("2026-10-02", "3", "2"), ("2026-10-03", "5", "4")])
        self.assertEqual(json.loads(second["badges/clones.json"])["message"], "10")

    def test_snapshots_keep_one_set_of_rows_a_day(self):
        a = stats.update({}, fetched(), REPO, "2026-10-01")
        b = stats.update(a, fetched(downloads=(("v0.1.0", "Sedjem-setup.exe", 12),)), REPO, "2026-10-01")  # same day again
        c = stats.update(b, fetched(downloads=(("v0.1.0", "Sedjem-setup.exe", 15),)), REPO, "2026-10-02")
        rows = stats.read_csv(c["downloads.csv"])
        self.assertEqual([(r["date"], r["asset"], r["downloads"]) for r in rows],
                         [("2026-10-01", "Sedjem-setup.exe", "12"), ("2026-10-02", "Sedjem-setup.exe", "15")])
        self.assertEqual(len(stats.read_csv(c["repo.csv"])), 2)

    def test_the_checksum_list_is_not_a_download(self):
        new = stats.update({}, fetched(downloads=(("v0.1.0", "Sedjem-setup.exe", 9), ("v0.1.0", "Sedjem-linux.tar.gz", 2),
                                                  ("v0.1.0", "SHA256SUMS.txt", 7))), REPO, "2026-10-01")
        self.assertEqual(json.loads(new["badges/downloads.json"]),
                         {"schemaVersion": 1, "label": "downloads", "message": "11", "color": stats.COLOUR})
        self.assertIn("| Downloads of the app, all releases | 11 |", new["README.md"])
        self.assertIn("| v0.1.0 | SHA256SUMS.txt | 7 |", new["README.md"], "still listed, just not counted")

    def test_drafts_are_left_out(self):
        f = fetched()
        f["releases"].append({"tag_name": "v0.2.0", "draft": True, "assets": [{"name": "x.exe", "download_count": 3}]})
        self.assertNotIn("v0.2.0", stats.update({}, f, REPO, "2026-10-01")["downloads.csv"])

    def test_without_traffic_access_the_old_traffic_is_kept_and_the_summary_says_so(self):
        first = stats.update({}, fetched(clones=[day("2026-10-01", 2, 2)], views=[day("2026-10-01", 9, 3)]), REPO, "2026-10-01")
        later = stats.update(first, fetched(traffic=False), REPO, "2026-10-05")
        for name in ("clones.csv", "views.csv", "referrers.csv"):
            self.assertEqual(later[name], first[name])
        self.assertIn("last read: 2026-10-01", later["README.md"])
        self.assertIn("STATS_TOKEN", later["README.md"])
        self.assertNotIn("could not be read", first["README.md"])

    def test_the_summary_shows_totals_and_the_last_days(self):
        new = stats.update({}, fetched(clones=[day("2026-10-01", 2, 2), day("2026-10-02", 1, 1)],
                                       views=[day("2026-10-01", 9, 3), day("2026-10-02", 4, 2)]), REPO, "2026-10-02")
        text = new["README.md"]
        self.assertTrue(text.startswith("# Sedjem statistics"))
        self.assertIn("| Clones since 2026-10-01 | 3 (3 unique per day, added up) |", text)
        self.assertIn("| Page views since 2026-10-01 | 13 (5 unique per day, added up) |", text)
        self.assertIn("| Stars, forks, watchers | 4, 1, 2 |", text)
        self.assertIn("| 2026-10-02 | 1 | 1 | 4 | 2 |", text)
        self.assertLess(text.index("| 2026-10-02 |"), text.index("| 2026-10-01 |"), "newest first")
        self.assertIn("| github.com | 6 | 3 |", text)
        self.assertNotIn("—", text)

    def test_an_unchanged_run_changes_no_file(self):
        f = fetched(clones=[day("2026-10-01", 2, 2)], views=[day("2026-10-01", 9, 3)], published={"v0.1.0": "2026-09-26"},
                    stargazers=["2026-09-28"], forks=[])
        once = stats.update({}, f, REPO, "2026-10-01")
        self.assertEqual(stats.update(once, f, REPO, "2026-10-01"), once)

    def test_the_star_history_comes_from_the_stargazers_and_forks(self):
        new = stats.update({}, fetched(stargazers=["2026-09-28", "2026-09-28", "2026-09-28", "2026-09-29"],
                                       forks=["2026-10-02"]), REPO, "2026-10-20")
        self.assertEqual([(r["date"], r["stars"], r["forks"]) for r in stats.read_csv(new["stars.csv"])],
                         [("2026-09-28", "3", "0"), ("2026-09-29", "4", "0"), ("2026-10-02", "4", "1")])
        # a day the lists could not be read keeps the history, and a day with none has none
        self.assertEqual(stats.update(new, fetched(), REPO, "2026-10-21")["stars.csv"], new["stars.csv"])
        self.assertEqual(stats.update(new, fetched(stargazers=[], forks=[]), REPO, "2026-10-21")["stars.csv"],
                         "date,stars,forks\n")


class Fetch(unittest.TestCase):
    def test_the_stats_token_reads_the_traffic_and_a_refusal_falls_back(self):
        calls = []

        def call(token, path, method="GET", body=None, raw=False, accept=None):
            calls.append((token, path.split("?")[0]))
            if "/traffic/" in path:
                if token == "stats-token":
                    raise stats.GitHubError(403, "Resource not accessible")
                return {"clones": [day("2026-10-01", 1, 1)]} if "clones" in path else \
                    {"views": [day("2026-10-01", 4, 2)]} if "views" in path else []
            return [] if path.startswith(f"/repos/{REPO}/releases") else {"stargazers_count": 0}

        with mock.patch.object(stats, "call", call), mock.patch("builtins.print"):
            got = stats.fetch(REPO, "actions-token", "stats-token")
        self.assertEqual(got["clones"], [day("2026-10-01", 1, 1)])
        self.assertEqual(calls[2], ("stats-token", f"/repos/{REPO}/traffic/clones"), "the stats token is tried first")
        self.assertIn(("actions-token", f"/repos/{REPO}/traffic/clones"), calls)

    def test_no_traffic_access_at_all_warns_and_records_the_rest(self):
        def call(token, path, method="GET", body=None, raw=False, accept=None):
            if "/traffic/" in path:
                raise stats.GitHubError(403, "no")
            return [] if "/releases" in path else {"stargazers_count": 0}

        with mock.patch.object(stats, "call", call), mock.patch("builtins.print") as out:
            got = stats.fetch(REPO, "actions-token", None)
        self.assertIsNone(got["clones"])
        self.assertIn("STATS_TOKEN", out.call_args[0][0])

    def test_stars_and_forks_are_read_with_their_days_a_hundred_to_a_request(self):
        calls = []

        def call(token, path, method="GET", body=None, raw=False, accept=None):
            calls.append((path, accept))
            page = int(re.search(r"[?&]page=(\d+)", path).group(1)) if "page=" in path else None
            if path.startswith(f"/repos/{REPO}/stargazers"):
                return [{"starred_at": "2026-09-28T16:33:08Z"}] * (100 if page == 1 else 3)
            if path.startswith(f"/repos/{REPO}/forks"):
                return [{"created_at": "2026-10-02T09:00:00Z"}]
            if "/traffic/" in path:
                return {"clones": [], "views": []} if "popular" not in path else []
            return [] if "/releases" in path else {"stargazers_count": 103, "forks_count": 1}

        with mock.patch.object(stats, "call", call):
            got = stats.fetch(REPO, "actions-token", None)
        self.assertEqual(len(got["stargazers"]), 103)
        self.assertEqual(len(got["forks"]), 1)
        star_calls = [(p, a) for p, a in calls if "/stargazers" in p]
        self.assertEqual([p for p, _ in star_calls], [f"/repos/{REPO}/stargazers?per_page=100&page=1",
                                                      f"/repos/{REPO}/stargazers?per_page=100&page=2"])
        self.assertTrue(all(a == "application/vnd.github.star+json" for _, a in star_calls), "starred_at needs it")
        self.assertIn((f"/repos/{REPO}/forks?sort=oldest&per_page=100&page=1", None), calls)

    def test_no_request_for_stars_or_forks_while_there_are_none_and_a_failure_keeps_the_history(self):
        def call(token, path, method="GET", body=None, raw=False, accept=None):
            self.assertNotIn("/forks", path)
            if "/stargazers" in path:
                raise stats.GitHubError(502, "bad gateway")
            if "/traffic/" in path:
                return {"clones": [], "views": []} if "popular" not in path else []
            return [] if "/releases" in path else {"stargazers_count": 2, "forks_count": 0}

        with mock.patch.object(stats, "call", call), mock.patch("builtins.print") as out:
            got = stats.fetch(REPO, "actions-token", None)
        self.assertIsNone(got["stargazers"])
        self.assertIsNone(got["forks"])
        self.assertIn("stars.csv is left as it was", out.call_args[0][0])
        self.assertEqual(got["clones"], [], "the rest is still read")


def chart(new, theme="light"):
    return ET.fromstring(new[stats.CHARTS[theme]])


def contrast(a, b):
    """WCAG's contrast ratio of two #rrggbb colours."""
    def luminance(c):
        r, g, b = (v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                   for v in (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class Plot:
    """A panel of a chart and its scales, read from its data-x and data-y attributes."""

    def __init__(self, root, name):
        self.g = next(g for g in root.iter(f"{SVG}g") if g.get("data-panel") == name)
        first, _, x0, slot = self.g.get("data-x").split()
        self.first, self.x0, self.slot = date.fromisoformat(first), float(x0), float(slot)
        top, base, top_y = self.g.get("data-y").split()
        self.top, self.base, self.top_y = int(top), float(base), float(top_y)

    def x(self, day):
        return self.x0 + (date.fromisoformat(day) - self.first).days * self.slot

    def y(self, value):
        return self.base - value * (self.base - self.top_y) / self.top

    def series(self, name):
        """[(day, value)] of a line, and the vertices of its path (None for a single dot)."""
        mark = next(e for e in self.g.iter() if e.get("data-series") == name)
        points = [(d, int(v)) for d, v in (p.split(":") for p in mark.get("data-points").split())]
        return points, (vertices(mark.get("d")) if mark.tag == f"{SVG}path" else None)

    def texts(self, **attr):
        return [e.text for e in self.g.iter(f"{SVG}text") if all(e.get(f"data-{k}") == v for k, v in attr.items())]


def vertices(d):
    return [(float(x), float(y)) for x, y in re.findall(r"(-?[\d.]+),(-?[\d.]+)", d)]


class Charts(unittest.TestCase):
    # Three days of the stats branch: the 0.1.0 installer and Linux archive (and the checksum list, which
    # doesn't count), then 0.2.0 on the second day; four stars, one fork; clones and views by day
    def history(self):
        new, published = {}, {"v0.1.0": "2026-09-26", "v0.2.0": "2026-10-19"}
        days = [("2026-10-18", (("v0.1.0", "Sedjem-setup.exe", 9), ("v0.1.0", "Sedjem-linux.tar.gz", 2), ("v0.1.0", "SHA256SUMS.txt", 1))),
                ("2026-10-19", (("v0.2.0", "Sedjem-setup.exe", 0), ("v0.1.0", "Sedjem-setup.exe", 9), ("v0.1.0", "Sedjem-linux.tar.gz", 3), ("v0.1.0", "SHA256SUMS.txt", 4))),
                ("2026-10-20", (("v0.2.0", "Sedjem-setup.exe", 4), ("v0.2.0", "SHA256SUMS.txt", 2), ("v0.1.0", "Sedjem-setup.exe", 10), ("v0.1.0", "Sedjem-linux.tar.gz", 3)))]
        for today, downloads in days:
            new = stats.update(new, fetched(
                downloads=downloads, published={t: published[t] for t, _, _ in downloads},
                clones=[day("2026-10-05", 0, 0), day("2026-10-17", 3, 3), day("2026-10-19", 52, 24)],
                views=[day("2026-10-05", 3, 2), day("2026-10-17", 15, 5), day("2026-10-19", 12, 6)],
                stargazers=["2026-09-28", "2026-09-28", "2026-09-28", "2026-09-29"], forks=["2026-10-02"]), REPO, today)
        return new

    def test_the_charts_are_valid_svg_with_a_title_and_a_description(self):
        new = self.history()
        for theme in stats.CHARTS:
            root = chart(new, theme)
            self.assertEqual(root.tag, f"{SVG}svg")
            self.assertEqual(root.get("viewBox"), f"0 0 {root.get('width')} {root.get('height')}")
            self.assertEqual(root.get("role"), "img")
            ids = {e.get("id"): e for e in root}
            self.assertEqual([ids[i].tag for i in root.get("aria-labelledby").split()], [f"{SVG}title", f"{SVG}desc"])
            self.assertIn("Sedjem on GitHub", root.find(f"{SVG}title").text)
            desc = root.find(f"{SVG}desc").text
            self.assertIn("as a running total: 17 on 20 October 2026, recorded since 18 October 2026", desc)
            self.assertIn("v0.1.0 on 26 September 2026, v0.2.0 on 19 October 2026", desc)
            self.assertIn("Stars: 4, the first on 28 September 2026. Forks: 1, the first on 2 October 2026.", desc)
            self.assertIn("Clones per day: 55 since 5 October 2026, the most 52 on 19 October 2026.", desc)
            # self-contained: nothing to run, load or fetch, and only the fonts the reader has
            text = new[stats.CHARTS[theme]]
            for banned in ("<script", "href", "url(", "@import", "<image", "<foreignObject", "—"):
                self.assertNotIn(banned, text)
            self.assertIn("system-ui", root.get("font-family"))

    def test_each_chart_is_on_a_card_of_its_own_so_it_reads_on_a_page_of_either_theme(self):
        # GitHub shows the other theme's chart where its theme markers don't apply (or both of them)
        new = self.history()
        for theme, colours in stats.THEMES.items():
            root = chart(new, theme)
            card = [e for e in root if e.tag not in (f"{SVG}title", f"{SVG}desc")][0]
            self.assertEqual(card.tag, f"{SVG}rect", "drawn first, under everything else")
            self.assertEqual((card.get("fill"), card.get("stroke")), (colours["card"], colours["edge"]))
            width, height = float(root.get("width")), float(root.get("height"))
            self.assertEqual([float(card.get(k)) for k in ("x", "y", "width", "height")],
                             [0.5, 0.5, width - 1, height - 1], "the whole chart")
            for e in root.iter(f"{SVG}text"):
                self.assertGreaterEqual(contrast(e.get("fill"), colours["card"]), 4.5, f"{theme}: {e.text}")
                x, anchor = float(e.get("x")), e.get("text-anchor", "start")
                if anchor != "middle":
                    self.assertTrue(stats.MARGIN <= x if anchor == "start" else x <= width - stats.MARGIN,
                                    f"{theme}: {e.text} keeps off the card's edge")

    def test_the_summary_on_the_branch_shows_them_for_the_readers_theme(self):
        summary = self.history()["README.md"]
        for theme, path in stats.CHARTS.items():
            self.assertIn(f'<a href="{path}#gh-{theme}-mode-only"><img src="{path}"', summary)
        self.assertNotIn("<picture>", summary, "it would follow the system's setting, not the theme chosen on GitHub")
        self.assertLess(summary.index("#gh-light-mode-only"), summary.index("## Totals"), "above the tables")
        self.assertTrue(all(path in stats.FILES for path in stats.CHARTS.values()),
                        "read back from the branch, so an unchanged chart isn't committed again")

    def test_downloads_are_a_running_total_of_the_app_with_the_releases_marked(self):
        p = Plot(chart(self.history()), "downloads")
        # 9 + 2, then 9 + 3 (+ 0 for 0.2.0), then 4 + 10 + 3; the checksum lists never count
        points, path = p.series("downloads")
        self.assertEqual(points, [("2026-10-18", 11), ("2026-10-19", 12), ("2026-10-20", 17)])
        for (d, v), (x, y) in zip(points, path):
            self.assertAlmostEqual(x, p.x(d), delta=0.01)
            self.assertAlmostEqual(y, p.y(v), delta=0.01)
        self.assertEqual(p.texts(label="downloads"), ["17"])
        self.assertGreaterEqual(p.top, 17)
        self.assertEqual(p.texts(note="downloads"), ["Recorded from 18 Oct"], "the record starts after 0.1.0")
        marks = {g.get("data-release"): g for g in p.g.iter(f"{SVG}g") if g.get("data-release")}
        self.assertEqual({t: g.get("data-date") for t, g in marks.items()}, {"v0.1.0": "2026-09-26", "v0.2.0": "2026-10-19"})
        for tag, g in marks.items():
            rule = g.find(f"{SVG}line")
            self.assertAlmostEqual(float(rule.get("x1")), p.x(g.get("data-date")), delta=0.01)
            self.assertAlmostEqual(float(rule.get("y2")), p.base, delta=0.01)
            self.assertEqual(g.find(f"{SVG}text").text, tag)
        self.assertEqual(p.first, date(2026, 9, 26), "the time axis starts at the first release")

    def test_stars_and_forks_rise_from_their_first_day(self):
        p = Plot(chart(self.history()), "stars")
        points, path = p.series("stars")
        self.assertEqual(points, [("2026-09-28", 3), ("2026-09-29", 4), ("2026-10-20", 4)])
        self.assertEqual(path[0], (round(p.x("2026-09-28"), 2), p.base), "from 0 on the day of the first star")
        for d, v in points:
            self.assertIn((round(p.x(d), 2), round(p.y(v), 2)), path)
        self.assertEqual(p.series("forks")[0], [("2026-10-02", 1), ("2026-10-20", 1)])
        self.assertIn("4 stars", p.texts(), "the legend gives the totals")
        self.assertIn("1 fork", p.texts())

    def test_clones_and_views_are_the_daily_numbers_with_their_uniques(self):
        root = chart(self.history())
        for name, rows in (("clones", [("2026-10-17", 3, 3), ("2026-10-19", 52, 24)]),
                           ("views", [("2026-10-05", 3, 2), ("2026-10-17", 15, 5), ("2026-10-19", 12, 6)])):
            p = Plot(root, name)
            columns = [g for g in p.g.iter(f"{SVG}g") if g.get("data-count")]
            self.assertEqual([(g.get("data-date"), int(g.get("data-count")), int(g.get("data-unique"))) for g in columns],
                             rows, "a column for each day with any, the days with none left empty")
            for g, (d, n, unique) in zip(columns, rows):
                segments = [vertices(s.get("d")) for s in g.iter(f"{SVG}path")]
                xs = [x for seg in segments for x, _ in seg]
                self.assertAlmostEqual((min(xs) + max(xs)) / 2, p.x(d), delta=0.01)
                self.assertAlmostEqual(min(y for seg in segments for _, y in seg), p.y(n), delta=0.01, msg="the count")
                front = segments[-1]
                self.assertAlmostEqual(min(y for _, y in front), p.y(unique), delta=0.01, msg="the uniques")
                self.assertAlmostEqual(max(y for _, y in front), p.base, delta=0.01)
            self.assertEqual(p.texts(label="most"), [str(max(n for _, n, _ in rows))])
        self.assertEqual(Plot(root, "clones").texts()[1], "55 in all since 5 Oct 2026")

    def test_light_and_dark_differ_only_in_their_colours(self):
        new = self.history()
        light, dark = new[stats.CHARTS["light"]], new[stats.CHARTS["dark"]]
        colour = re.compile(r"#[0-9a-fA-F]{6}\b")
        self.assertEqual(colour.sub("#", light), colour.sub("#", dark))
        self.assertNotEqual(light, dark)
        for theme, svg in (("light", light), ("dark", dark)):
            self.assertLessEqual(set(colour.findall(svg)), set(stats.THEMES[theme].values()), theme)
        self.assertEqual(stats.THEMES["light"]["accent"], "#0b7f7d", "the README's teal")
        self.assertEqual(stats.THEMES["dark"]["accent"], "#14a3a1")

    def test_a_sparse_or_empty_history_still_draws(self):
        # the very first run: one day of downloads, one star today, no forks, traffic of one day
        first = stats.update({}, fetched(published={"v0.1.0": "2026-10-20"}, stargazers=["2026-10-20"], forks=[],
                                         clones=[day("2026-10-20", 2, 1)], views=[day("2026-10-20", 5, 2)]), REPO, "2026-10-20")
        for theme in stats.CHARTS:
            root = chart(first, theme)
            p = Plot(root, "downloads")
            points, path = p.series("downloads")
            self.assertEqual((points, path), ([("2026-10-20", 9)], None), "a dot, not a line")
            self.assertEqual(p.texts(label="downloads"), ["9"])
            self.assertEqual(Plot(root, "stars").texts(label="stars"), ["1"])
            self.assertIn("1 star", Plot(root, "stars").texts())
            self.assertIn("No forks yet", Plot(root, "stars").texts())
        # nothing at all: no releases, no stars, no traffic read
        empty = stats.update({}, {"releases": [], "repo": {"stargazers_count": 0, "forks_count": 0, "subscribers_count": 0},
                                  "stargazers": [], "forks": [], "clones": None, "views": None, "referrers": None}, REPO, "2026-10-20")
        root = chart(empty, "dark")
        self.assertEqual({e.text for e in root.iter(f"{SVG}text") if e.get("data-empty")},
                         {"No downloads recorded yet", "No stars or forks yet", "Not recorded yet"})
        self.assertFalse([e for e in root.iter() if e.get("data-series") or e.get("data-count")])
        # traffic recorded, all of it zero
        zero = stats.update({}, fetched(clones=[day("2026-10-05", 0, 0)], views=[day("2026-10-05", 0, 0)]), REPO, "2026-10-20")
        self.assertEqual(Plot(chart(zero), "views").texts(empty="1"), ["No views since 5 Oct 2026"])

    def test_a_long_history_keeps_the_time_axis_readable(self):
        start = date(2026, 1, 1)
        clones = [day(f"{date.fromordinal(start.toordinal() + i)}", i % 7, i % 3) for i in range(800)]
        new = stats.update({}, fetched(clones=clones, views=clones, published={"v0.1.0": "2026-01-01"},
                                       stargazers=["2026-02-01"] * 30, forks=[]), REPO, "2028-03-10")
        p = Plot(chart(new), "clones")
        labels = [e for e in p.g.iter(f"{SVG}text") if e.get("y") == str(stats.num(p.base + 16))]
        self.assertGreater(len(labels), 2)
        xs = [float(e.get("x")) for e in labels]
        self.assertGreaterEqual(min(b - a for a, b in zip(xs, xs[1:])), 60, "room between the dates")
        self.assertIn("Jan 2027", [e.text for e in labels])


class StepSummary(unittest.TestCase):
    def test_a_run_into_a_folder_writes_every_file_and_the_job_summary_shows_the_charts(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            env = {"GITHUB_TOKEN": "t", "GITHUB_STEP_SUMMARY": str(summary)}
            with mock.patch.dict(os.environ, env), mock.patch.object(stats, "read_branch", return_value=(None, {})), \
                    mock.patch.object(stats, "fetch", return_value=fetched(stargazers=[], forks=[])), \
                    mock.patch("builtins.print"):
                stats.main(["--repo", REPO, "--out", str(Path(tmp) / "out")])
            for path in stats.FILES:
                self.assertTrue((Path(tmp) / "out" / path).is_file(), path)
            text = summary.read_text(encoding="utf-8")
            for path in stats.CHARTS.values():
                self.assertIn(f'="https://raw.githubusercontent.com/{REPO}/{stats.BRANCH}/{path}"', text)
            self.assertNotIn('="charts/', text, "the job summary has no branch to resolve a relative path against")


class Wiring(unittest.TestCase):
    def test_the_workflow_never_clones_and_runs_this_script(self):
        flow = (ROOT / ".github" / "workflows" / "stats.yml").read_text(encoding="utf-8")
        self.assertNotIn("actions/checkout", flow)
        self.assertNotRegex(flow, r"\bgit (clone|fetch|pull)\b")
        fetched = re.search(r"contents/(\S+?)\?ref=", flow).group(1)
        self.assertEqual(fetched, ".github/scripts/repo_stats.py")
        self.assertTrue((ROOT / fetched).is_file())
        self.assertIn("secrets.STATS_TOKEN", flow)
        self.assertIn("contents: write", flow)

    def test_the_script_needs_only_the_standard_library(self):
        tree = ast.parse((ROOT / ".github" / "scripts" / "repo_stats.py").read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(names - set(sys.stdlib_module_names), set(), "the runner has no packages installed")

    def test_the_readme_badges_read_the_files_the_script_writes(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("downloads", "clones"):
            path = f"badges/{name}.json"
            self.assertIn(path, stats.FILES)
            self.assertIn(f"%2F{stats.BRANCH}%2F{path.replace('/', '%2F')}", readme, f"the {name} badge")
        self.assertIn(f"/tree/{stats.BRANCH}#readme", readme, "the badges link to the summary")

    def test_the_readme_charts_are_files_the_script_writes(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        raw = r'https://raw\.githubusercontent\.com/MohammedEl-sayedAhmed/sedjem/([^/"]+)/([^"]+)"'
        written = stats.update({}, fetched(), REPO, "2026-10-20")
        for theme in stats.CHARTS:
            found = re.search(rf'<a href="([^"#]+)#gh-{theme}-mode-only"><img src="{raw}', readme)
            self.assertIsNotNone(found, f"the {theme} chart, in a link with GitHub's marker for its theme")
            link, branch, path = found.groups()
            self.assertTrue(link.endswith(f"/blob/{stats.BRANCH}/README.md"), "linked to the summary")
            self.assertEqual((branch, path), (stats.BRANCH, stats.CHARTS[theme]))
            self.assertIn(path, stats.FILES, "read back from the branch, so an unchanged chart isn't committed again")
            self.assertIn(path, written)

    def test_the_readme_logo_and_charts_follow_the_theme_chosen_on_github(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("prefers-color-scheme", readme, "a <picture> goes by the system's light or dark setting, "
                         "which can differ from the theme chosen on GitHub")
        for theme, logo in (("light", "sedjem-logo-colour.svg"), ("dark", "sedjem-logo-colour-dark.svg")):
            self.assertIn(f'<a href="docs/brand/{logo}#gh-{theme}-mode-only"><img src="docs/brand/{logo}"', readme)
            self.assertTrue((ROOT / "docs" / "brand" / logo).is_file(), logo)


if __name__ == "__main__":
    unittest.main()
