"""Tests for releases (.github/scripts/release.py, docs/14-pipeline.md), against a stand-in for GitHub's API and in
throwaway git repositories. A version is released only when it is X.Y.Z, matches app/__init__.py, is newer than
every release and has no release yet, from a commit on main whose Tests run passed; its tag is made once and kept
when the release runs again. The notes are the file in .github/release-notes/ when there is one, else a draft from
the titles of the commits since the previous release, grouped by area, which a dry run only prints.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".github" / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("release", SCRIPTS / "release.py")
assert spec and spec.loader
rel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rel)
pp = rel.pp

REPO = "owner/sedjem"
C = "c" * 40   # the commit to release
D = "d" * 40   # another commit
T = "7" * 40   # an annotated tag object


def release(tag, draft=False, prerelease=None):
    return {"id": hash(tag) % 10_000, "tag_name": tag, "draft": draft,
            "prerelease": "-" in tag if prerelease is None else prerelease}


def run(commit=C, conclusion="success", event="push", branch="main", repo=REPO):
    return {"head_sha": commit, "event": event, "head_branch": branch, "conclusion": conclusion,
            "head_repository": {"full_name": repo}}


class FakeGitHub:
    """The calls release.py makes. `tags` maps a tag to the commit it is on, or to ("tag", commit) for an annotated
    one; `on_main` is the compare status of main against the commit."""

    def __init__(self, releases=(release("v0.3.1"), release("v0.3.2-preview.20261030")), runs=(run(),), tags=None,
                 on_main="ahead"):
        self.releases, self.runs, self.on_main = list(releases), list(runs), on_main
        self.tags = dict(tags or {})
        self.calls = []

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path, body))
        url = urlsplit(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert url.path.startswith(f"/repos/{REPO}/"), path
        p = url.path[len(f"/repos/{REPO}"):]
        if (method, p) == ("GET", "/releases"):
            page, per = int(query.get("page", 1)), int(query["per_page"])
            return self.releases[(page - 1) * per: page * per]
        if (method, p) == ("GET", "/actions/workflows/tests.yml/runs"):
            return {"workflow_runs": [r for r in self.runs if r["head_sha"] == query["head_sha"]]}
        if method == "GET" and p.startswith("/git/ref/tags/"):
            name = unquote(p[len("/git/ref/tags/"):])
            if name not in self.tags:
                raise pp.GitHubError(404, "Not Found")
            target = self.tags[name]
            return {"object": {"type": "tag", "sha": T} if isinstance(target, tuple) else {"type": "commit", "sha": target}}
        if method == "GET" and p == f"/git/tags/{T}":
            return {"object": {"type": "commit", "sha": next(t[1] for t in self.tags.values() if isinstance(t, tuple))}}
        if method == "GET" and p.startswith("/compare/"):
            base, head = (unquote(x) for x in p[len("/compare/"):].split("..."))
            assert head == "main", path
            return {"status": self.on_main}
        if (method, p) == ("POST", "/git/refs"):
            self.tags[body["ref"].removeprefix("refs/tags/")] = body["sha"]
            return {"ref": body["ref"], "object": {"sha": body["sha"]}}
        raise AssertionError(f"unexpected call {method} {path}")


class Check(unittest.TestCase):
    def check(self, gh=None, version="0.3.2", commit=C, app="0.3.2"):
        return rel.check(version, commit, app, REPO, gh or FakeGitHub())

    def refused(self, why, **kw):
        with self.assertRaises(ValueError) as e:
            self.check(**kw)
        self.assertIn(why, str(e.exception))

    def test_a_tested_commit_on_main_with_the_apps_version_is_released(self):
        gh = FakeGitHub()
        self.assertEqual(self.check(gh), {"tag": "v0.3.2", "previous": "v0.3.1", "tagged": False})
        runs = next(path for _, path, _ in gh.calls if "/runs" in path)
        self.assertIn(f"head_sha={C}", runs)
        self.assertIn("event=push", runs)
        self.assertIn("branch=main", runs)
        self.assertFalse([c for c in gh.calls if c[0] != "GET"], "a check changes nothing")

    def test_the_version(self):
        for version in ("0.3.2-preview.20261030", "v0.3.2", "0.3", "0.3.2.1", ""):
            with self.subTest(version):
                self.refused("is not a release version", version=version, app=version)
        self.refused("doesn't match the version in app/__init__.py", app="0.3.1")
        self.refused("v0.3.1 has a release already", version="0.3.1", app="0.3.1")
        self.refused("is not newer than v0.3.1", version="0.3.0", app="0.3.0")
        self.refused("v0.3.2 has a release already", gh=FakeGitHub(releases=[release("v0.3.1"), release("v0.3.2", draft=True)]))

    def test_the_commit(self):
        self.refused("is not the full sha", commit="ccccccc")
        for status in ("behind", "diverged"):
            with self.subTest(status):
                self.refused("is not on main", gh=FakeGitHub(on_main=status))
        self.assertEqual(self.check(FakeGitHub(on_main="identical"))["tag"], "v0.3.2")

    def test_only_a_passed_run_of_the_push_to_main(self):
        for runs in ([], [run(conclusion="failure")], [run(conclusion=None)], [run(event="pull_request")],
                     [run(branch="feature")], [run(repo="someone/sedjem")], [run(D)]):
            with self.subTest(runs):
                self.refused("the Tests workflow hasn't passed", gh=FakeGitHub(runs=runs))
        self.check(FakeGitHub(runs=[run(conclusion="failure"), run()]))  # passed when run again

    def test_a_tag_already_on_the_commit_is_kept_and_on_another_one_stops_it(self):
        self.assertTrue(self.check(FakeGitHub(tags={"v0.3.2": C}))["tagged"])
        self.assertTrue(self.check(FakeGitHub(tags={"v0.3.2": ("tag", C)}))["tagged"], "through an annotated tag")
        self.refused(f"the tag v0.3.2 is on {D[:7]}", gh=FakeGitHub(tags={"v0.3.2": D}))

    def test_the_first_release(self):
        self.assertEqual(self.check(FakeGitHub(releases=[]))["previous"], "")


class Tag(unittest.TestCase):
    def test_the_commit_is_tagged_once(self):
        gh = FakeGitHub()
        self.assertEqual(rel.tag("0.3.2", C, REPO, gh), f"{C[:7]} is tagged v0.3.2")
        self.assertEqual([c for c in gh.calls if c[0] == "POST"],
                         [("POST", f"/repos/{REPO}/git/refs", {"ref": "refs/tags/v0.3.2", "sha": C})])
        self.assertEqual(rel.tag("0.3.2", C, REPO, gh), f"v0.3.2 is on {C[:7]} already")
        self.assertEqual(len([c for c in gh.calls if c[0] == "POST"]), 1)

    def test_a_dry_run_tags_nothing(self):
        gh = FakeGitHub()
        self.assertEqual(rel.tag("0.3.2", C, REPO, gh, dry_run=True), f"{C[:7]} would be tagged v0.3.2")
        self.assertEqual([c for c in gh.calls if c[0] != "GET"], [])

    def test_a_tag_on_another_commit_stops_it(self):
        with self.assertRaisesRegex(ValueError, "is on dddddd"):
            rel.tag("0.3.2", C, REPO, FakeGitHub(tags={"v0.3.2": D}))

    def test_the_api_sends_a_body_as_json(self):
        sent = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            sent.append(request)
            return Answer(b'{"ref": "refs/tags/v0.3.2"}')

        with mock.patch("urllib.request.urlopen", urlopen):
            answer = pp.call("t0ken", f"/repos/{REPO}/git/refs", "POST", {"ref": "refs/tags/v0.3.2", "sha": C})
            pp.call("t0ken", f"/repos/{REPO}/releases")
        self.assertEqual(answer, {"ref": "refs/tags/v0.3.2"})
        post, get = sent
        self.assertEqual((post.get_method(), json.loads(post.data)), ("POST", {"ref": "refs/tags/v0.3.2", "sha": C}))
        self.assertEqual(post.get_header("Content-type"), "application/json")
        self.assertEqual((get.get_method(), get.data, get.get_header("Content-type")), ("GET", None, None))


def git_env(folder):
    """git without the user's or the system's settings, with a fixed author."""
    empty = Path(folder) / "gitconfig"
    empty.write_text("", encoding="utf-8")
    return {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Sedjem Tests", "GIT_AUTHOR_EMAIL": "tests@example.com",
            "GIT_COMMITTER_NAME": "Sedjem Tests", "GIT_COMMITTER_EMAIL": "tests@example.com"}


class Notes(unittest.TestCase):
    """A throwaway repository: v0.2.0, a preview tag, then the commits of 0.3.0 (with a merge), and v0.3.0."""

    TITLES = ["Updates: a pill when something new is out", "Website: a page of its own",
              "Tests: the update checks against a stand-in for GitHub", "Assistants: an MCP server (#9)",
              "Updates: macOS isn't offered a Windows installer", "fix a typo in the README",
              "Docs: the updates", "Dependencies: Bump requests from 2.34.2 to 2.35.0",
              "Updates: app.py starts again on its new code", "Sedjem 0.3.0: the version, and the release notes"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.env = git_env(self.tmp.name)
        self.minutes = 0
        self.git("init", "-q", "-b", "main", str(self.repo), cwd=self.tmp.name)
        self.commit("Speaker labels: in 0.2.0, never in later notes")
        self.commit("Version 0.2.0: the notes")
        self.git("tag", "v0.2.0")
        self.commit("Nightly previews: a pre-release of main")
        self.git("tag", "v0.2.1-preview.20261021")
        for title in self.TITLES[:5]:
            self.commit(title)
        self.git("checkout", "-q", "-b", "side")
        self.commit(self.TITLES[5])
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "side", "-m", "Merge branch 'side'")
        for title in self.TITLES[6:]:
            self.commit(title)
        self.git("tag", "v0.3.0")
        self.commit("Usage counts: after 0.3.0")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def commit(self, title):
        """A commit a minute after the one before, so that the order of the history is clear."""
        self.minutes += 1
        when = f"2026-10-01T10:{self.minutes:02d}:00+0000"
        with mock.patch.dict(self.env, {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}):
            self.git("commit", "-q", "--allow-empty", "-m", title, "-m", "A body, which the notes leave out.")

    def notes(self, version, commit="HEAD", dry_run=True):
        with mock.patch.dict(os.environ, self.env):
            return rel.notes(version, commit, REPO, root=self.repo, dry_run=dry_run)

    def test_the_previous_release_is_the_newest_release_tag_before_the_version(self):
        with mock.patch.dict(os.environ, self.env):
            self.assertEqual(rel.previous_release("0.3.0", "v0.3.0", cwd=self.repo), "v0.2.0", "not the preview")
            self.assertEqual(rel.previous_release("0.3.1", "HEAD", cwd=self.repo), "v0.3.0")
            self.assertEqual(rel.previous_release("0.2.0", "HEAD", cwd=self.repo), None)
            self.assertEqual(rel.titles("v0.2.0", "v0.3.0", cwd=self.repo),
                             ["Nightly previews: a pre-release of main", *self.TITLES], "oldest first, no merges")

    def test_a_draft_groups_the_titles_by_area(self):
        text, done = self.notes("0.3.0", "v0.3.0")
        self.assertEqual(done, "drafted from 11 commits since v0.2.0")
        changes = text[text.index("## Changes"):]
        self.assertEqual([line for line in changes.splitlines() if line.startswith("#")],
                         ["## Changes", "### Assistants", "### Nightly previews", "### Updates", "### Other changes",
                          "### Tests, docs and the pipeline"])
        updates = changes[changes.index("### Updates"):changes.index("### Other changes")]
        self.assertEqual(updates.split("\n\n")[1].splitlines(),
                         ["- A pill when something new is out.", "- macOS isn't offered a Windows installer.",
                          "- app.py starts again on its new code."], "in order, as sentences, names kept as written")
        self.assertIn("### Other changes\n\n- Fix a typo in the README.\n", changes)
        self.assertIn("### Assistants\n\n- An MCP server (#9).\n", changes)
        self.assertIn("### Tests, docs and the pipeline\n\n- Website: a page of its own.\n"
                      "- Tests: the update checks against a stand-in for GitHub.\n- Docs: the updates.\n"
                      "- Dependencies: Bump requests from 2.34.2 to 2.35.0.\n", changes)
        for left_out in ("Sedjem 0.3.0: the version", "Merge branch", "in 0.2.0, never", "after 0.3.0", "A body"):
            self.assertNotIn(left_out, text)
        self.assertIn(f"[v0.2.0...v0.3.0](https://github.com/{REPO}/compare/v0.2.0...v0.3.0)", text)

    def test_a_draft_has_the_download_section_and_the_licence(self):
        text, _ = self.notes("0.3.0", "v0.3.0")
        self.assertTrue(text.startswith("Sedjem 0.3.0, with the changes since 0.2.0.\n\n## Download\n"))
        for part in ("`Sedjem-0.3.0-setup.exe` installs for your user", "*More info*, then *Run anyway*",
                     "Installing over 0.2.0 keeps", "`Sedjem-0.3.0-linux-x64.tar.gz`", "`SHA256SUMS.txt`",
                     "free software under the AGPL-3.0", "commercial licences are available from the author"):
            self.assertIn(part, text)
        self.assertNotIn("can update itself", text, "0.2.0 can't")
        self.assertIn("0.3.0 can update itself: *Settings → Updates*, then *Update now*.", self.notes("0.3.1")[0])
        self.assertNotIn("**", text)
        self.assertNotIn("—", text)

    def test_the_file_written_for_a_release_is_used_as_it_is(self):
        folder = self.repo / ".github" / "release-notes"
        folder.mkdir(parents=True)
        (folder / "v0.3.0.md").write_text("Written by hand.\n", encoding="utf-8")
        for dry_run in (True, False):
            text, done = self.notes("0.3.0", "v0.3.0", dry_run=dry_run)
            self.assertEqual(text, "Written by hand.\n")
            self.assertIn("are used as they are", done)
        self.assertEqual((folder / "v0.3.0.md").read_text(encoding="utf-8"), "Written by hand.\n")

    def test_a_dry_run_writes_nothing_and_otherwise_the_draft_is_written(self):
        path = self.repo / ".github" / "release-notes" / "v0.3.1.md"
        text, _ = self.notes("0.3.1")
        self.assertFalse(path.exists())
        written, done = self.notes("0.3.1", dry_run=False)
        self.assertEqual((written, path.read_text(encoding="utf-8")), (text, text))
        self.assertTrue(done.endswith("into .github/release-notes/v0.3.1.md"))
        self.assertIn("### Usage counts\n\n- After 0.3.0.\n", text)

    def test_nothing_since_the_previous_release(self):
        text, _ = self.notes("0.3.1", "v0.3.0^{commit}")
        self.assertIn("Nothing has changed since the previous release.", text)


class Repository(unittest.TestCase):
    def test_the_script_needs_only_the_standard_library(self):
        tree = ast.parse((SCRIPTS / "release.py").read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(names - set(sys.stdlib_module_names), {"preview_plan"}, "and the script beside it")

    def test_it_reads_the_apps_version_and_this_releases_notes(self):
        from app import __version__
        self.assertEqual(rel.app_version(), __version__)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rel.main(["notes", __version__, "--dry-run"])
        self.assertEqual(out.getvalue(), (ROOT / ".github" / "release-notes" / f"v{__version__}.md").read_text(encoding="utf-8"))

    def test_the_command_line_refuses_what_isnt_a_release(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            rel.main(["notes", "0.3.2-preview.20261030", "--dry-run"])
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}), self.assertRaises(SystemExit):
            rel.main(["check", "0.3.2", "--commit", C])


if __name__ == "__main__":
    unittest.main()
