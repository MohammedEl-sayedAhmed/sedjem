"""Tests for the Footprints check (.github/scripts/check_footprints.py, ADR 15): the AI-tool attribution that it
finds in a commit's message, trailers and added lines and in a pull request's title and description, the plain
text that it leaves alone, the files that may name the patterns, a bot's description, and a run over a real
repository made for the test. The test file names the patterns on purpose; the check skips it
(.github/scripts/check_footprints.py, ALLOWED). Offline, standard library only; the last test needs git.
Run: .venv/bin/python -m unittest tests.test_check_footprints -v
"""
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_footprints", ROOT / ".github" / "scripts" / "check_footprints.py")
assert spec and spec.loader
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

FOUND = [
    "Co-authored-by: Claude <noreply@anthropic.com>",
    "co-authored-by: ChatGPT",
    "Co-Authored-By: Some Assistant <helper@openai.com>",
    "Signed-off-by: Bot <bot@mail.anthropic.com>",
    "Generated with Claude Code",
    "Generated with [Claude Code](https://example.org)",
    "written by Gemini",
    "Created using an AI assistant",
    "drafted with AI",
    "Powered by Copilot",
    "with the help of Claude",
    "Assisted-by: Codex",
    "Reviewed-by: claude",
    "https://claude.ai/code/session_1",
    "https://claude.com/product/claude-code",
    "\U0001F916 Generated",
    "&#129302; done",
    ":robot_face: done",
    "Co-authored​-by: Cl​aude",  # invisible characters are removed first
    "Co authored by: Claude",
]
CLEAN = [
    "Updates: ask first, then restart",
    "Co-authored-by: Jane Doe <jane@example.org>",
    "Signed-off-by: Jane Doe <jane@example.org>",
    "A painting by Claude Monet, and the anthropic principle",
    "see https://www.anthropic.com/news for the vendor's page",
    "the Gemini constellation, a copilot seat and a codex of laws",
    "built with Python, written by hand, made by the team",
    "Generated files are listed in .gitignore",
    "Assisted by the test suite",
    "",
]


class Patterns(unittest.TestCase):
    def test_what_is_found(self):
        for text in FOUND:
            with self.subTest(text=text):
                self.assertTrue(fp.scan(text), "a footprint")

    def test_what_is_left_alone(self):
        for text in CLEAN:
            with self.subTest(text=text):
                self.assertEqual(fp.scan(text), [])

    def test_a_footprint_in_a_longer_text_is_reported_with_its_line(self):
        found = fp.scan("Updates: one thing\n\nWhat changed.\n\nCo-authored-by: Claude <x@y.z>\n")
        self.assertEqual(found, [("co-author", "Co-authored-by: Claude <x@y.z>")])

    def test_only_added_lines_outside_the_allowed_files_count(self):
        diff = ("diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n@@ -1 +1,2 @@\n"
                "-Generated with Claude Code\n+ok = 1\n+# Generated with Claude Code\n"
                "diff --git a/tests/test_check_footprints.py b/tests/test_check_footprints.py\n"
                "--- a/tests/test_check_footprints.py\n+++ b/tests/test_check_footprints.py\n@@ -0,0 +1 @@\n"
                "+Generated with Claude Code\n"
                "diff --git a/docs/y.md b/docs/y.md\n--- a/docs/y.md\n+++ b/docs/y.md\n@@ -0,0 +1 @@\n+plain\n")
        self.assertEqual(fp.added_lines(diff), "ok = 1\n# Generated with Claude Code\nplain")
        for path in fp.ALLOWED:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_the_allowed_files_are_the_check_and_its_test_only(self):
        self.assertEqual(set(fp.ALLOWED), {".github/scripts/check_footprints.py", "tests/test_check_footprints.py"})


@unittest.skipUnless(shutil.which("git"), "no git")
class Repository(unittest.TestCase):
    """check() and main() over a repository made here: a base commit, and the commits of a pull request."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_AUTHOR_NAME": "A",
               "GIT_AUTHOR_EMAIL": "a@example.org", "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@example.org"}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        chdir = os.getcwd()
        os.chdir(self.path)
        self.addCleanup(os.chdir, chdir)
        self.git("init", "-q", "-b", "main")
        self.commit("base", {"a.txt": "one\n"})
        self.base = self.git("rev-parse", "HEAD").strip()

    @staticmethod
    def git(*args):
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout

    def commit(self, message, files):
        for name, body in files.items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text(body, encoding="utf-8")
            self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def run_check(self, **env):
        out = io.StringIO()
        head = self.git("rev-parse", "HEAD").strip()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            status = fp.main([self.base, head])
        return status, out.getvalue()

    def test_a_clean_pull_request_passes(self):
        self.commit("Updates: a thing", {"a.txt": "one\ntwo\n"})
        self.commit("Docs: another", {"b.txt": "text\n"})
        status, out = self.run_check(PR_TITLE="Updates: a thing", PR_BODY="Closes #1.")
        self.assertEqual((status, out), (0, "No AI-tool attribution in 2 commit(s), the title or the description.\n"))

    def test_a_trailer_in_a_message_fails(self):
        self.commit("Updates: a thing\n\nCo-authored-by: Claude <noreply@anthropic.com>", {"a.txt": "two\n"})
        status, out = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn("(message)", out)
        self.assertIn("[co-author]", out)

    def test_an_added_line_fails_but_not_in_an_allowed_file_or_a_removed_one(self):
        self.commit("Updates: quote", {"notes.md": "Generated with Claude Code\n"})
        status, out = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn("(added lines)", out)
        self.commit("Updates: gone again", {"notes.md": "fine\n"})
        self.assertEqual(self.run_check()[0], 1, "the first commit still adds it, since each commit is scanned")
        self.git("reset", "-q", "--hard", self.base)
        self.commit("Updates: allowed", {".github/scripts/check_footprints.py": "# Generated with Claude Code\n"})
        self.assertEqual(self.run_check()[0], 0)

    def test_the_title_and_the_description_fail_but_a_bots_description_does_not(self):
        self.commit("Updates: a thing", {"a.txt": "two\n"})
        self.assertEqual(self.run_check(PR_TITLE="Updates \U0001F916")[0], 1)
        status, out = self.run_check(PR_BODY="Generated with Claude Code")
        self.assertEqual(status, 1)
        self.assertIn("the pull request description", out)
        self.assertEqual(self.run_check(PR_BODY="Powered by Claude", PR_AUTHOR_TYPE="Bot")[0], 0)
        self.assertEqual(self.run_check(PR_TITLE="Powered by Claude", PR_AUTHOR_TYPE="Bot")[0], 1, "the title always counts")

    def test_an_unknown_range_is_an_error(self):
        self.commit("Updates: a thing", {"a.txt": "two\n"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = fp.main(["0" * 40, "1" * 40])
        self.assertEqual(status, 2)
        self.assertIn("can't be listed", out.getvalue())

    def test_the_command_line_needs_two_commits(self):
        with self.assertRaises(SystemExit):
            fp.main(["only-one"])


if __name__ == "__main__":
    unittest.main()
