"""Tests for the repository statistics (.github/scripts/repo_stats.py): the daily history grows past
GitHub's 14 days, a day reported again replaces the old numbers, snapshots keep one set of rows a day,
the checksum list isn't counted as a download, the badges and summary show the totals, and a run without
access to the traffic keeps the old traffic files and says so. The star history is rebuilt from the
stargazers and forks, which are read a hundred to a request. The workflow never clones the repository (a
clone would count itself) and the script needs only the standard library; the README's badges read the files
the script writes. Offline: the GitHub calls are replaced.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("repo_stats", ROOT / ".github" / "scripts" / "repo_stats.py")
assert spec and spec.loader
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)

REPO = "owner/sedjem"


def day(d, count, uniques):
    return {"timestamp": f"{d}T00:00:00Z", "count": count, "uniques": uniques}


def fetched(clones=(), views=(), downloads=(("v0.1.0", "Sedjem-setup.exe", 9), ("v0.1.0", "SHA256SUMS.txt", 1)),
            referrers=(("github.com", 6, 3),), traffic=True, stargazers=None, forks=None):
    """What fetch() returns. stargazers and forks: the days of each star and fork (None: not read)."""
    releases = {}
    for tag, name, n in downloads:
        releases.setdefault(tag, []).append({"name": name, "download_count": n})
    return {"releases": [{"tag_name": t, "assets": a} for t, a in releases.items()],
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
        f = fetched(clones=[day("2026-10-01", 2, 2)], views=[day("2026-10-01", 9, 3)])
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


if __name__ == "__main__":
    unittest.main()
