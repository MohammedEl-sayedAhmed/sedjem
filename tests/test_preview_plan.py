"""Tests for the nightly previews (.github/scripts/preview_plan.py), against a stand-in for GitHub's API.
A preview is built only when main's newest commit whose tests passed is in neither the newest release nor the
newest preview; commits whose tests failed or are still running, commits no longer on main and pull requests
from forks are never built. A preview's version is the next patch after the newest release and the date (.2,
.3 for more the same day), and sorts between releases; the desktop build accepts exactly those, and releases
that match app/__init__.py. Previews and their tags are removed after a week, the newest kept and releases
never touched. Also the wiring: the workflows' triggers, permissions and calls, and the installer's update mode.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import importlib.util
import itertools
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

try:
    import yaml
except ImportError:  # the workflow checks then read the text only
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("preview_plan", ROOT / ".github" / "scripts" / "preview_plan.py")
assert spec and spec.loader
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)

REPO = "owner/sedjem"
DAY = "20261021"
NOW = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
IDS = itertools.count(1)


def sha(name):
    """A commit sha that reads as its name in a failure: sha("C") = 4300…0."""
    return (name.encode().hex() + "0" * 40)[:40]


def release(tag, commit=None, prerelease=None, draft=False, days_ago=1):
    """A release as GET /repos/{repo}/releases gives it; a tag with a hyphen is a pre-release unless told."""
    when = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": next(IDS), "tag_name": tag, "draft": draft, "prerelease": "-" in tag if prerelease is None else prerelease,
            "target_commitish": commit or "main", "created_at": when, "published_at": None if draft else when}


def run(commit, conclusion="success", event="push", branch="main", repo=REPO):
    return {"head_sha": commit, "event": event, "head_branch": branch, "conclusion": conclusion,
            "status": "completed" if conclusion else "in_progress", "head_repository": {"full_name": repo}}


class FakeGitHub:
    """The API calls the script makes, answered from what a test sets up. `relations` maps (base, head) to the
    compare status. With filters=False, the runs come back without the query's filters applied, to check that
    the script doesn't rely on them. Commits in `older` predate the script."""

    def __init__(self, releases=(), runs=(), relations=None, tags=(), filters=True, older=()):
        self.releases = list(releases)
        self.runs = list(runs)
        self.relations = dict(relations or {})
        self.tags = set(tags) | {r["tag_name"] for r in self.releases if not r["draft"]}
        self.filters = filters
        self.older = set(older)
        self.calls = []

    def __call__(self, path, method="GET"):
        self.calls.append((method, path))
        url = urlsplit(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert url.path.startswith(f"/repos/{REPO}/"), path
        p = url.path[len(f"/repos/{REPO}"):]
        if (method, p) == ("GET", "/releases"):
            page, per = int(query.get("page", 1)), int(query["per_page"])
            return self.releases[(page - 1) * per: page * per]
        if (method, p) == ("GET", "/actions/workflows/tests.yml/runs"):
            runs = [r for r in self.runs if not self.filters or (
                r["head_branch"] == query["branch"] and r["event"] == query["event"] and r["conclusion"] == query["status"])]
            return {"total_count": len(runs), "workflow_runs": runs[:int(query["per_page"])]}
        if m := re.fullmatch(r"/compare/([^/]+)\.\.\.([^/]+)", p):
            base, head = unquote(m[1]), unquote(m[2])
            if base == head:
                return {"status": "identical"}
            if (base, head) not in self.relations:
                raise pp.GitHubError(404, f"no relation set up for {base}...{head}")
            return {"status": self.relations[(base, head)]}
        if (method, p) == ("GET", f"/contents/{pp.SELF}"):
            if query["ref"] in self.older:
                raise pp.GitHubError(404, "Not Found")
            return {"type": "file", "path": pp.SELF}
        if m := re.fullmatch(r"/git/matching-refs/tags/(.+)", p):
            return [{"ref": f"refs/tags/{t}"} for t in sorted(self.tags) if t.startswith(unquote(m[1]))]
        if method == "DELETE" and (m := re.fullmatch(r"/releases/(\d+)", p)):
            self.releases = [r for r in self.releases if r["id"] != int(m[1])]
            return None
        if method == "DELETE" and (m := re.fullmatch(r"/git/refs/tags/(.+)", p)):
            if unquote(m[1]) not in self.tags:
                raise pp.GitHubError(422, "Reference does not exist")
            self.tags.discard(unquote(m[1]))
            return None
        raise AssertionError(f"unexpected call {method} {path}")


def nightly(**changes):
    """main moved past the last preview (built yesterday from P) and the 0.2.0 release (R), and C passed."""
    setup = {"releases": [release("v0.2.0", days_ago=2), release("v0.2.1-preview.20261020", sha("P"))],
             "runs": [run(sha("C"))],
             "relations": {(sha("C"), "main"): "identical", ("v0.2.0", sha("C")): "ahead", (sha("P"), sha("C")): "ahead"}}
    setup.update(changes)
    return FakeGitHub(**setup)


class Versions(unittest.TestCase):
    def test_previews_sort_between_the_releases(self):
        expected = ["0.2.0", "0.2.1-preview.20261021", "0.2.1-preview.20261021.2", "0.2.1-preview.20261021.10",
                    "0.2.1-preview.20261022", "0.2.1", "0.10.0"]
        self.assertEqual(sorted(reversed(expected), key=pp.order), expected)

    def test_other_names_are_not_versions(self):
        for name in ("0.2", "v0.2.0", "0.2.0-rc.1", "0.2.0-preview.2026102", "0.2.0-preview.20261021.0",
                     "0.2.0-preview.20261021.", "", None):
            with self.subTest(name=name):
                self.assertIsNone(pp.order(name))
        self.assertIsNone(pp.tag_version("0.2.0"), "a tag starts with v")

    def test_the_numbers_of_the_day(self):
        self.assertEqual(pp.preview_version("0.2.0", DAY, set()), "0.2.1-preview.20261021")
        self.assertEqual(pp.preview_version("0.2.0", DAY, {"v0.2.1-preview.20261021"}), "0.2.1-preview.20261021.2")
        self.assertEqual(pp.preview_version("0.2.0", DAY, {"v0.2.1-preview.20261021", "v0.2.1-preview.20261021.2"}),
                         "0.2.1-preview.20261021.3")
        self.assertEqual(pp.preview_version("0.2.0", DAY, {"v0.2.1-preview.20261021.3"}), "0.2.1-preview.20261021.4",
                         "always after the last one, even when an earlier one was removed")
        self.assertEqual(pp.preview_version("0.2.0", DAY, {"v0.2.1-preview.20261020.5", "v0.2.0"}), "0.2.1-preview.20261021",
                         "another day's previews don't count")


class Plan(unittest.TestCase):
    def test_main_moved_and_its_tests_passed_so_a_preview_is_built(self):
        got = pp.plan(REPO, nightly(), DAY)
        self.assertTrue(got["build"])
        self.assertEqual((got["version"], got["commit"]), ("0.2.1-preview.20261021", sha("C")))
        self.assertIn("v0.2.1-preview.20261020", got["why"])

    def test_nothing_is_built_twice_or_backwards(self):
        for status in ("identical", "behind"):
            with self.subTest(status=status):
                gh = nightly(relations={(sha("C"), "main"): "identical", ("v0.2.0", sha("C")): "ahead",
                                        (sha("P"), sha("C")): status})
                got = pp.plan(REPO, gh, DAY)
                self.assertFalse(got["build"])
                self.assertIn("already in the preview v0.2.1-preview.20261020", got["why"])

    def test_a_commit_the_newest_release_contains_is_not_built(self):
        gh = nightly(releases=[release("v0.2.0")], relations={(sha("C"), "main"): "identical", ("v0.2.0", sha("C")): "identical"})
        got = pp.plan(REPO, gh, DAY)
        self.assertFalse(got["build"])
        self.assertIn("already in the release v0.2.0", got["why"])

    def test_after_history_was_rewritten_the_new_main_is_built(self):
        gh = nightly(relations={(sha("C"), "main"): "ahead", ("v0.2.0", sha("C")): "ahead", (sha("P"), sha("C")): "diverged"})
        self.assertTrue(pp.plan(REPO, gh, DAY)["build"])

    def test_failed_and_running_commits_are_never_built(self):
        runs = [run(sha("D"), conclusion=None), run(sha("F"), conclusion="failure"),
                run(sha("X"), conclusion="cancelled"), run(sha("C"))]
        for filters in (True, False):
            with self.subTest(filters=filters):
                got = pp.plan(REPO, nightly(runs=runs, filters=filters), DAY)
                self.assertEqual(got["commit"], sha("C"))

    def test_the_runs_asked_for_are_pushes_to_main_that_passed(self):
        gh = nightly()
        pp.plan(REPO, gh, DAY)
        path = next(p for _, p in gh.calls if "/actions/workflows/" in p)
        query = parse_qs(urlsplit(path).query)
        self.assertEqual({k: query[k] for k in ("branch", "event", "status")},
                         {"branch": ["main"], "event": ["push"], "status": ["success"]})

    def test_a_pull_request_from_a_fork_named_main_is_never_built(self):
        runs = [run(sha("E"), event="pull_request", repo="someone/sedjem"), run(sha("G"), repo="someone/sedjem"),
                run(sha("H"), branch="feature"), run(sha("C"))]
        got = pp.plan(REPO, nightly(runs=runs, filters=False), DAY)
        self.assertEqual(got["commit"], sha("C"))

    def test_a_commit_no_longer_on_main_is_not_built(self):
        for status in ("diverged", "behind"):
            with self.subTest(status=status):
                got = pp.plan(REPO, nightly(relations={(sha("C"), "main"): status}), DAY)
                self.assertFalse(got["build"])
                self.assertIn("no longer on main", got["why"])

    def test_a_commit_from_before_nightly_previews_is_not_built(self):
        got = pp.plan(REPO, nightly(older={sha("C")}), DAY)
        self.assertFalse(got["build"], "it has no build stamp, no installer update mode and no version check")
        self.assertIn("older than nightly previews", got["why"])

    def test_nothing_to_follow_or_nothing_tested(self):
        got = pp.plan(REPO, nightly(releases=[release("v0.2.1-preview.20261020", sha("P"))]), DAY)
        self.assertFalse(got["build"])
        self.assertIn("no release yet", got["why"])
        got = pp.plan(REPO, nightly(runs=[run(sha("F"), conclusion="failure")]), DAY)
        self.assertFalse(got["build"])
        self.assertIn("passed the Tests workflow", got["why"])

    def test_the_version_follows_the_newest_release_by_version(self):
        releases = [release("v0.2.5", days_ago=1), release("v0.3.0", days_ago=20),  # a later fix of the older line
                    release("v0.4.0-rc.1"), release("v0.5.0", draft=True, prerelease=False),
                    release("v0.9.9", prerelease=True)]
        gh = nightly(releases=releases, relations={(sha("C"), "main"): "identical", ("v0.3.0", sha("C")): "ahead"})
        self.assertEqual(pp.plan(REPO, gh, DAY)["version"], "0.3.1-preview.20261021")

    def test_a_second_preview_the_same_day_is_numbered(self):
        gh = nightly(releases=[release("v0.2.0"), release("v0.2.1-preview.20261021", sha("P"))])
        self.assertEqual(pp.plan(REPO, gh, DAY)["version"], "0.2.1-preview.20261021.2")
        # a tag left without its release (a publication that failed) still takes its number
        gh = nightly(releases=[release("v0.2.0"), release("v0.2.1-preview.20261021", sha("P"))],
                     tags={"v0.2.1-preview.20261021.2"})
        self.assertEqual(pp.plan(REPO, gh, DAY)["version"], "0.2.1-preview.20261021.3")

    def test_a_preview_without_a_commit_target_is_compared_by_its_tag(self):
        gh = nightly(releases=[release("v0.2.0"), release("v0.2.1-preview.20261020", "main")],
                     relations={(sha("C"), "main"): "identical", ("v0.2.0", sha("C")): "ahead",
                                ("v0.2.1-preview.20261020", sha("C")): "identical"})
        self.assertFalse(pp.plan(REPO, gh, DAY)["build"])

    def test_a_draft_preview_is_not_the_newest(self):
        gh = nightly(releases=[release("v0.2.0"), release("v0.2.1-preview.20261020", sha("P")),
                               release("v0.2.1-preview.20261021", sha("C"), draft=True)])
        got = pp.plan(REPO, gh, DAY)
        self.assertTrue(got["build"], "a publication that failed half-way doesn't count as built")
        self.assertEqual(got["version"], "0.2.1-preview.20261021.2", "but its name is taken")

    def test_every_page_of_releases_is_read(self):
        releases = [release(f"v0.1.{i}") for i in range(150)]  # the newest is on the second page
        gh = nightly(releases=releases, relations={(sha("C"), "main"): "identical", ("v0.1.149", sha("C")): "ahead"})
        self.assertEqual(pp.plan(REPO, gh, DAY)["version"], "0.1.150-preview.20261021")


class Check(unittest.TestCase):
    def offline(self, path, method="GET"):
        raise AssertionError("a release is checked without GitHub")

    def test_a_release_must_match_the_app(self):
        self.assertEqual(pp.check("0.2.0", "0.2.0", REPO, self.offline), ("stable", ""))
        with self.assertRaisesRegex(ValueError, r"doesn't match the version in app/__init__.py \(0.2.0\)"):
            pp.check("0.2.1", "0.2.0", REPO, self.offline)

    def test_a_preview_must_be_the_next_patch_after_the_newest_release(self):
        gh = FakeGitHub(releases=[release("v0.2.0"), release("v0.1.0"), release("v0.2.1-preview.20261020", sha("P"))])
        for version in ("0.2.1-preview.20261021", "0.2.1-preview.20261021.2"):
            self.assertEqual(pp.check(version, "0.2.0", REPO, gh), ("preview", "v0.2.0"))
        self.assertEqual(pp.check("0.2.1-preview.20261021", "0.3.0", REPO, gh), ("preview", "v0.2.0"),
                         "the app's version may already name the coming release")
        for version in ("0.2.0-preview.20261021", "0.2.2-preview.20261021", "0.3.0-preview.20261021"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "is 0.2.1-preview.YYYYMMDD"):
                pp.check(version, "0.2.0", REPO, gh)

    def test_anything_else_is_refused(self):
        gh = FakeGitHub(releases=[release("v0.2.0")])
        for version in ("0.2.1-beta", "latest", "v0.2.1", "0.2.1-preview.2026102", "0.2.1-preview.20261341"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                pp.check(version, "0.2.0", REPO, gh)
        with self.assertRaisesRegex(ValueError, "no release yet"):
            pp.check("0.2.1-preview.20261021", "0.2.0", REPO, FakeGitHub())


class Notes(unittest.TestCase):
    def test_a_preview_says_where_it_comes_from_and_that_nobody_tried_it(self):
        text = pp.notes(REPO, "0.2.1-preview.20261021", sha("C"), "v0.2.0")
        url = f"https://github.com/{REPO}"
        self.assertIn(f"on 21 October 2026 from commit [{sha('C')[:7]}]({url}/commit/{sha('C')}) on main", text)
        self.assertIn(f"({url}/compare/v0.2.0...{sha('C')})", text)
        self.assertIn("nobody has tried this build by hand", text)
        self.assertIn(f"({url}/releases/latest)", text)
        for name in ("Sedjem-0.2.1-preview.20261021-setup.exe", "Sedjem-0.2.1-preview.20261021-linux-x64.tar.gz",
                     "SHA256SUMS.txt"):
            self.assertIn(f"`{name}`", text)
        self.assertNotIn("—", text)
        self.assertNotIn("**", text)


class Prune(unittest.TestCase):
    def test_previews_go_after_a_week_with_their_tags_and_releases_stay(self):
        gh = FakeGitHub(releases=[release("v0.2.0", days_ago=30), release("v0.1.0", days_ago=60),
                                  release("v0.2.1-preview.20261011", sha("A"), days_ago=10),
                                  release("v0.2.1-preview.20261013", sha("B"), days_ago=8),
                                  release("v0.2.1-preview.20261018", sha("C"), days_ago=3),
                                  release("v0.2.1-preview.20261020", sha("D"), days_ago=1)])
        gone = pp.prune(REPO, gh, NOW)
        self.assertEqual(sorted(gone), ["v0.2.1-preview.20261011", "v0.2.1-preview.20261013"])
        kept = {"v0.2.0", "v0.1.0", "v0.2.1-preview.20261018", "v0.2.1-preview.20261020"}
        self.assertEqual({r["tag_name"] for r in gh.releases}, kept)
        self.assertEqual(gh.tags, kept)

    def test_the_newest_preview_stays_however_old(self):
        gh = FakeGitHub(releases=[release("v0.2.0", days_ago=40), release("v0.2.1-preview.20260920", sha("A"), days_ago=31),
                                  release("v0.2.1-preview.20261001", sha("B"), days_ago=20)])
        self.assertEqual(pp.prune(REPO, gh, NOW), ["v0.2.1-preview.20260920"])
        self.assertIn("v0.2.1-preview.20261001", gh.tags)

    def test_releases_and_other_pre_releases_are_never_touched(self):
        untouched = [release("v0.1.0", days_ago=100), release("v0.3.0-rc.1", days_ago=50),
                     release("v0.2.1-preview.20260101", sha("A"), prerelease=False, days_ago=300),  # published as a release
                     release("v0.2.1-preview.20261020", sha("B"), days_ago=1)]
        gh = FakeGitHub(releases=untouched)
        self.assertEqual(pp.prune(REPO, gh, NOW), [])
        self.assertEqual(len(gh.releases), 4)
        self.assertNotIn("DELETE", {m for m, _ in gh.calls})

    def test_a_stale_draft_goes_without_the_tag_of_the_preview_named_like_it(self):
        gh = FakeGitHub(releases=[release("v0.2.0", days_ago=30), release("v0.2.1-preview.20261012", sha("A"), draft=True, days_ago=9),
                                  release("v0.2.1-preview.20261012", sha("A"), days_ago=9),
                                  release("v0.2.1-preview.20261020", sha("B"), days_ago=1)])
        self.assertEqual(pp.prune(REPO, gh, NOW), ["v0.2.1-preview.20261012", "v0.2.1-preview.20261012"])
        self.assertNotIn("v0.2.1-preview.20261012", gh.tags)
        draft_only = FakeGitHub(releases=[release("v0.2.1-preview.20261012", sha("A"), draft=True, days_ago=9),
                                          release("v0.2.1-preview.20261020", sha("B"), days_ago=1)],
                                tags={"v0.2.1-preview.20261012"})  # a tag of that name, which the draft doesn't own
        pp.prune(REPO, draft_only, NOW)
        self.assertIn("v0.2.1-preview.20261012", draft_only.tags)
        self.assertEqual([r["tag_name"] for r in draft_only.releases], ["v0.2.1-preview.20261020"])

    def test_a_tag_already_gone_is_no_error(self):
        gh = FakeGitHub(releases=[release("v0.2.1-preview.20261001", sha("A"), days_ago=20),
                                  release("v0.2.1-preview.20261020", sha("B"), days_ago=1)])
        gh.tags.discard("v0.2.1-preview.20261001")
        self.assertEqual(pp.prune(REPO, gh, NOW), ["v0.2.1-preview.20261001"])

    def test_a_dry_run_deletes_nothing(self):
        gh = FakeGitHub(releases=[release("v0.2.1-preview.20261001", sha("A"), days_ago=20),
                                  release("v0.2.1-preview.20261020", sha("B"), days_ago=1)])
        self.assertEqual(pp.prune(REPO, gh, NOW, dry_run=True), ["v0.2.1-preview.20261001"])
        self.assertEqual(len(gh.releases), 2)
        self.assertNotIn("DELETE", {m for m, _ in gh.calls})


def workflow(name):
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def triggers(data):
    return data.get("on", data.get(True))  # YAML 1.1 reads a bare `on` as true


def steps(job):
    return {s.get("name") or s.get("uses"): s for s in job["steps"]}


class Wiring(unittest.TestCase):
    def test_the_script_needs_only_the_standard_library(self):
        tree = ast.parse((ROOT / ".github" / "scripts" / "preview_plan.py").read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(names - set(sys.stdlib_module_names), set(), "the runner has no packages installed")

    def test_the_nightly_reads_without_cloning_and_builds_through_the_desktop_build(self):
        flow = workflow("preview.yml")
        self.assertNotIn("actions/checkout", flow, "deciding and pruning need no clone")
        fetched = set(re.findall(r"contents/(\S+?)\?ref=\$GITHUB_SHA", flow))
        self.assertEqual(fetched, {".github/scripts/preview_plan.py"})
        if yaml is None:
            self.skipTest("no PyYAML")
        data = yaml.safe_load(flow)
        self.assertEqual(set(triggers(data)), {"schedule", "workflow_dispatch"})
        self.assertEqual(data["permissions"], {"contents": "read"})
        self.assertEqual(data["concurrency"]["cancel-in-progress"], False)
        jobs = data["jobs"]
        self.assertEqual(jobs["plan"]["permissions"], {"contents": "read", "actions": "read"})
        self.assertEqual(jobs["build"]["uses"], "./.github/workflows/desktop.yml")
        self.assertEqual(jobs["build"]["with"], {"version": "${{ needs.plan.outputs.version }}",
                                                 "commit": "${{ needs.plan.outputs.commit }}"})
        self.assertIn("needs.plan.outputs.build == 'true'", jobs["build"]["if"])
        for name in ("build", "prune"):
            self.assertEqual(jobs[name]["permissions"], {"contents": "write"}, name)
        self.assertIn("prune", next(iter(steps(jobs["prune"]).values()))["run"])

    def test_the_desktop_build_publishes_previews_as_pre_releases(self):
        if yaml is None:
            self.skipTest("no PyYAML")
        data = yaml.safe_load(workflow("desktop.yml"))
        on = triggers(data)
        self.assertEqual(set(on), {"workflow_dispatch", "push", "pull_request", "workflow_call"})
        self.assertEqual(set(on["workflow_call"]["inputs"]), {"version", "commit"})
        self.assertEqual(data["permissions"], {"contents": "read"})
        jobs = data["jobs"]
        writers = [name for name, job in jobs.items() if "write" in str(job.get("permissions", {}).values())]
        self.assertEqual(writers, ["release"], "only publishing may write")
        self.assertIn("preview_plan.py check", steps(jobs["version"])["Version"]["run"])
        build = steps(jobs["build"])
        self.assertEqual(build["Build"]["env"], {"SEDJEM_VERSION": "${{ needs.version.outputs.version }}",
                                                 "SEDJEM_CHANNEL": "${{ needs.version.outputs.channel }}"})
        checkout = next(s for s in jobs["build"]["steps"] if s.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ needs.version.outputs.commit }}")
        publish = steps(jobs["release"])["Publish"]["run"]
        for part in ("--prerelease", '--target "$COMMIT"', "preview_plan.py notes", "SHA256SUMS.txt"):
            self.assertIn(part, publish)

    def test_the_windows_build_updates_an_installation_as_sedjem_does(self):
        if yaml is None:
            self.skipTest("no PyYAML")
        step = steps(yaml.safe_load(workflow("desktop.yml"))["jobs"]["build"])["Installer update mode (Windows)"]
        for part in ('"/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/UPDATE=1"', "/api/quit",
                     '"X-Sedjem" = "1"', "unins000.exe", "keep.txt", "WaitForExit"):
            self.assertIn(part, step["run"])

    def test_the_installer_starts_sedjem_after_an_update_only(self):
        iss = (ROOT / "desktop" / "installer.iss").read_text(encoding="utf-8")
        run = re.search(r"^\[Run\]\n(.*?)(?=^\[)", iss, re.S | re.M).group(1)
        entries = [line for line in run.splitlines() if line.startswith("Filename:")]
        self.assertEqual(len(entries), 2)
        by_hand, update = entries
        self.assertIn("postinstall skipifsilent", by_hand)
        self.assertTrue(by_hand.endswith("Check: not IsUpdate"))
        self.assertNotIn("postinstall", update)
        self.assertNotIn("skipifsilent", update)
        self.assertTrue(update.endswith("Check: IsUpdate"))
        self.assertIn("ExpandConstant('{param:UPDATE|0}') = '1'", iss)
        self.assertRegex(iss, r'\[InstallDelete\]\n(;.*\n)*Type: filesandordirs; Name: "\{app\}\\_internal"\n')
        # setup deletes nothing of the data folder, and writes only the Privacy page's choice, beside Sedjem.exe
        deletes = re.findall(r"^\[(?:Install|Uninstall)Delete\]\n(.*?)(?=^\[)", iss, re.S | re.M)
        self.assertNotIn("localappdata", "".join(deletes).lower(), "setup never deletes from the data folder")
        code = iss.split("\n[Code]\n")[1]
        for call in ("DeleteFile(", "DelTree(", "RenameFile(", "RemoveDir(", "SaveStringsTo"):
            self.assertNotIn(call, code)
        self.assertEqual(code.count("SaveStringToFile("), 1)


if __name__ == "__main__":
    unittest.main()
