"""Tests for the CodeQL ratchet (.github/scripts/codeql_ratchet.py, docs/14-pipeline.md): a finding's fingerprint
survives lines added above it, a copy of a known finding is new, a pull request fails only on findings that the
baseline lacks, a missing baseline branch passes with a notice, and the baseline is written only when it isn't
already from a newer commit. Offline: the GitHub calls are replaced. Standard library only.
Run: .venv/bin/python -m unittest tests.test_codeql_ratchet -v
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("codeql_ratchet", ROOT / ".github" / "scripts" / "codeql_ratchet.py")
assert spec and spec.loader
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)

REPO = "owner/sedjem"
OLD, NEW = "a" * 40, "b" * 40


def result(rule, path="app/x.py", line=3, digest="h1"):
    out = {"ruleId": rule, "locations": [{"physicalLocation": {"artifactLocation": {"uri": path},
                                                               "region": {"startLine": line}}}]}
    if digest:
        out["partialFingerprints"] = {"primaryLocationLineHash": digest}
    return out


def sarif(folder, *results, name="python.sarif"):
    (Path(folder) / name).write_text(json.dumps({"runs": [{"results": list(results)}]}), encoding="utf-8")


class Fingerprints(unittest.TestCase):
    def test_the_hash_survives_a_moved_line_and_the_fallback_does_not(self):
        self.assertEqual(ratchet.fingerprint(result("py/a", line=3)), ratchet.fingerprint(result("py/a", line=90)))
        self.assertEqual(ratchet.fingerprint(result("py/a")), "py/a:h1")
        self.assertEqual(ratchet.fingerprint(result("py/a", digest=None)), "py/a:app/x.py:3")
        self.assertNotEqual(ratchet.fingerprint(result("py/a", digest=None, line=4)),
                            ratchet.fingerprint(result("py/a", digest=None)))
        self.assertEqual(ratchet.fingerprint({}), "?:?:?")

    def test_every_file_under_the_folder_counts_and_a_copy_counts_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "sub").mkdir()
            sarif(tmp, result("py/a"), result("py/a", path="app/y.py"), result("py/b", digest="h2"))
            sarif(Path(tmp) / "sub", result("js/c", digest="h3"), name="javascript.sarif")
            (Path(tmp) / "notes.txt").write_text("not sarif", encoding="utf-8")
            counts, places, files = ratchet.findings(tmp)
        self.assertEqual(files, 2)
        self.assertEqual(counts, {"py/a:h1": 2, "py/b:h2": 1, "js/c:h3": 1})
        self.assertEqual(places["py/a:h1"], ("app/x.py", 3), "the first place stands for the fingerprint")

    def test_only_what_the_baseline_has_fewer_of_is_new(self):
        current = ratchet.Counter({"a": 2, "b": 1, "c": 1})
        self.assertEqual(ratchet.new_findings(current, {"a": 1, "b": 1, "z": 5}), {"a": 1, "c": 1})
        self.assertEqual(ratchet.new_findings(current, {"a": 2, "b": 3, "c": 1}), {})

    def test_a_line_break_in_a_name_starts_no_other_command(self):
        self.assertEqual(ratchet.escape("a%b\n::error::x\r"), "a%25b%0A::error::x%0D")

    def test_the_baseline_file_is_stable(self):
        text = ratchet.baseline_text(ratchet.Counter({"b": 1, "a": 2}), OLD, "2026-11-07")
        self.assertEqual(json.loads(text), {"commit": OLD, "updated": "2026-11-07", "total": 3,
                                            "findings": {"a": 2, "b": 1}})
        self.assertLess(text.index('"a"'), text.index('"b"'))
        self.assertTrue(text.endswith("\n"))


class Commands(unittest.TestCase):
    def run_command(self, command, folder, baseline, extra=(), compare="diverged", fail=None, env=None):
        """main()'s exit status, what it printed and the calls it made to GitHub. baseline: None for no branch, False
        for a branch without the file, else the file's content."""
        calls = []

        def call(token, path, method="GET", body=None, raw=False):
            calls.append((method, path, body))
            if path.endswith(f"/git/ref/heads/{ratchet.BRANCH}"):
                if baseline is None:
                    raise ratchet.GitHubError(404, "Not Found")
                return {"object": {"sha": "h" * 40}}
            if "/contents/" in path:
                if baseline is False:
                    raise ratchet.GitHubError(404, "Not Found")
                return json.dumps(baseline)
            if "/compare/" in path:
                return {"status": compare}
            if method == "GET":
                return {"tree": {"sha": "tree-of-" + path.rsplit("/", 1)[-1]}}
            if fail and method == fail[0] and path.endswith(fail[1]):
                raise ratchet.GitHubError(422, "not a fast forward")
            return {"sha": "n" * 40}

        out = io.StringIO()
        argv = [command, "--sarif", folder, "--repo", REPO, *extra]
        environ = {k: v for k, v in os.environ.items() if k != "RATCHET_ARMED"}
        environ.update({"GITHUB_TOKEN": "t", **(env or {})})
        with mock.patch.object(ratchet, "call", call), mock.patch.dict("os.environ", environ, clear=True), \
                contextlib.redirect_stdout(out):
            status = ratchet.main(argv)
        return status, out.getvalue(), calls

    def test_a_pull_request_with_no_new_finding_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/a", line=40), result("py/b", digest="h2"))
            known = {"commit": OLD, "findings": {"py/a:h1": 1, "py/b:h2": 1, "py/gone:h9": 1}}
            status, out, calls = self.run_command("check", tmp, known)
        self.assertEqual(status, 0)
        self.assertIn("New: 0", out)
        self.assertEqual({c[0] for c in calls}, {"GET"}, "a check never writes")

    def test_a_pull_request_that_adds_a_finding_fails_and_says_where(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/a"), result("py/new", path="app/z.py", line=7, digest="h5"))
            status, out, _ = self.run_command("check", tmp, {"commit": OLD, "findings": {"py/a:h1": 1}})
        self.assertEqual(status, 1)
        self.assertIn("::error file=app/z.py,line=7::py/new", out)
        self.assertIn("1 new CodeQL finding(s)", out)

    def test_a_copy_of_a_known_finding_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/a"), result("py/a", path="app/copy.py"))
            status, out, _ = self.run_command("check", tmp, {"commit": OLD, "findings": {"py/a:h1": 1}})
        self.assertEqual(status, 1)
        self.assertIn("::error file=app/x.py,line=3::py/a", out, "one of the copies, the first place that is known")

    def test_no_baseline_branch_passes_with_a_notice_until_the_ratchet_is_armed(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/a"), result("py/b", digest="h2"))
            status, out, calls = self.run_command("check", tmp, None)
            self.assertEqual(status, 0)
            self.assertIn("::notice::No security-baseline branch yet", out)
            self.assertIn("2 finding(s)", out)
            self.assertEqual({c[0] for c in calls}, {"GET"})
            for armed in ("true", "True"):
                with self.subTest(armed=armed):
                    status, out, calls = self.run_command("check", tmp, None, env={"RATCHET_ARMED": armed})
                    self.assertEqual(status, 1, "after the first baseline, a missing branch was deleted")
                    self.assertIn("::error::The security-baseline branch is missing", out)
                    self.assertEqual({c[0] for c in calls}, {"GET"})

    def test_a_baseline_branch_without_its_file_fails(self):
        """Only the CodeQL workflow writes the branch, and always with the file."""
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/a"))
            for env in (None, {"RATCHET_ARMED": "true"}):
                with self.subTest(env=env):
                    status, out, calls = self.run_command("check", tmp, False, env=env)
                    self.assertEqual(status, 1)
                    self.assertIn("::error::The security-baseline branch has no baseline.json", out)
                    self.assertEqual({c[0] for c in calls}, {"GET"})

    def test_an_analysis_without_results_files_fails_rather_than_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, out, _ = self.run_command("check", tmp, None)
        self.assertEqual(status, 1)
        self.assertIn("No SARIF files", out)

    def test_main_creates_the_baseline_branch_without_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/a"))
            status, out, calls = self.run_command("update", tmp, None, ["--commit", NEW])
        self.assertEqual(status, 0)
        writes = [c for c in calls if c[0] == "POST"]
        self.assertEqual([c[1].split("/")[-1] for c in writes], ["trees", "commits", "refs"])
        self.assertEqual(writes[1][2]["parents"], [])
        self.assertEqual(writes[2][2]["ref"], "refs/heads/security-baseline")
        stored = json.loads(writes[0][2]["tree"][0]["content"])
        self.assertEqual((stored["commit"], stored["findings"]), (NEW, {"py/a:h1": 1}))

    def test_each_baseline_commit_is_built_on_the_main_commits_tree(self):
        """GitHub reads a push's workflows from the pushed commit, so the branch must carry main's files, the
        guard workflow included; the parent is still the branch's own head."""
        for baseline in (None, {"commit": OLD, "findings": {}}, False):
            with self.subTest(baseline=baseline), tempfile.TemporaryDirectory() as tmp:
                sarif(tmp, result("py/a"))
                status, _, calls = self.run_command("update", tmp, baseline, ["--commit", NEW])
                self.assertEqual(status, 0)
                tree = next(c for c in calls if c[0] == "POST" and c[1].endswith("/git/trees"))[2]
                self.assertEqual(tree["base_tree"], "tree-of-" + NEW)
                self.assertEqual([e["path"] for e in tree["tree"]], ["baseline.json"])
                self.assertIn(("GET", f"/repos/{REPO}/git/commits/{NEW}", None), calls)
                commit = next(c for c in calls if c[0] == "POST" and c[1].endswith("/git/commits"))[2]
                self.assertEqual(commit["tree"], "n" * 40)
                self.assertEqual(commit["parents"], [] if baseline is None else ["h" * 40])

    def test_main_refreshes_the_baseline_on_top_of_its_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/b", digest="h2"))
            status, _, calls = self.run_command("update", tmp, {"commit": OLD, "findings": {"py/a:h1": 1}},
                                                ["--commit", NEW])
        self.assertEqual(status, 0)
        commit = next(c for c in calls if c[1].endswith("/git/commits") and c[0] == "POST")
        self.assertEqual(commit[2]["parents"], ["h" * 40])
        self.assertTrue(any(c[0] == "PATCH" for c in calls))

    def test_an_older_commit_never_replaces_a_newer_baseline_and_same_findings_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/b", digest="h2"))
            status, out, calls = self.run_command("update", tmp, {"commit": OLD, "findings": {}}, ["--commit", NEW],
                                                  compare="behind")
            self.assertEqual(status, 0)
            self.assertIn("stays", out)
            self.assertFalse([c for c in calls if c[0] != "GET"])
            status, out, calls = self.run_command("update", tmp, {"commit": OLD, "findings": {"py/b:h2": 1}},
                                                  ["--commit", NEW], compare="ahead")
            self.assertEqual(status, 0)
            self.assertIn("nothing to write", out)
            self.assertFalse([c for c in calls if c[0] != "GET"])

    def test_the_weekly_run_on_the_same_commit_writes_what_new_queries_find(self):
        """main has not moved since the baseline, but a newer query set finds more: the baseline must take it in,
        or every pull request would fail on it until the next push to main."""
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/a"), result("py/new", digest="h5"))
            status, out, calls = self.run_command("update", tmp, {"commit": NEW, "findings": {"py/a:h1": 1}},
                                                  ["--commit", NEW], compare="identical")
        self.assertEqual(status, 0)
        self.assertNotIn("stays", out)
        tree = next(c for c in calls if c[0] == "POST" and c[1].endswith("/git/trees"))[2]
        self.assertEqual(json.loads(tree["tree"][0]["content"])["findings"], {"py/a:h1": 1, "py/new:h5": 1})
        self.assertTrue(any(c[0] == "PATCH" for c in calls))

    def test_a_branch_that_moved_is_read_again_and_a_failure_that_lasts_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/b", digest="h2"))
            args = ("update", tmp, {"commit": OLD, "findings": {}}, ["--commit", NEW])
            with self.assertRaises(ratchet.GitHubError):
                self.run_command(*args, fail=("PATCH", ratchet.BRANCH))
            with self.assertRaises(ratchet.GitHubError):
                self.run_command(*args, fail=("POST", "/git/trees"))

    def test_the_branch_without_its_file_is_filled_in_on_top_of_its_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            sarif(tmp, result("py/a"))
            status, _, calls = self.run_command("update", tmp, False, ["--commit", NEW])
        self.assertEqual(status, 0)
        commit = next(c for c in calls if c[1].endswith("/git/commits") and c[0] == "POST")
        self.assertEqual(commit[2]["parents"], ["h" * 40], "a new root commit would be refused: the ref exists")


class Script(unittest.TestCase):
    def test_the_script_needs_only_the_standard_library(self):
        import re
        import sys
        source = (ROOT / ".github" / "scripts" / "codeql_ratchet.py").read_text(encoding="utf-8")
        self.assertLessEqual(set(re.findall(r"^(?:import|from) (\w+)", source, re.M)), set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
