"""Tests for the pipeline (docs/14-pipeline.md): the workflows' triggers, jobs, permissions and secrets, the pinned
actions, the quick checks that run before the slower jobs, Dependabot, the issue and pull request templates, and
.github/scripts/run_tests.py, which runs the suite in CI and writes its summary. The workflow checks need PyYAML,
as in tests/test_preview_plan.py; the rest is offline, standard library only.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import fnmatch
import importlib.util
import io
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # the workflow checks then skip
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
spec = importlib.util.spec_from_file_location("run_tests", ROOT / ".github" / "scripts" / "run_tests.py")
assert spec and spec.loader
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

UNPINNED = set()  # every workflow's actions are pinned
# What may write, and where: everything else reads at most
WRITERS = {
    ("desktop.yml", "release"): {"contents"},
    ("preview.yml", "build"): {"contents"}, ("preview.yml", "prune"): {"contents"},
    ("release.yml", "tag"): {"contents"}, ("release.yml", "build"): {"contents"},
    ("codeql.yml", "analyze"): {"security-events"},
    ("stats.yml", None): {"contents"},
    ("pages.yml", None): {"pages", "id-token"}, ("pages.yml", "on-main"): {"actions"},
}
REQUIRED_CHECKS = {"checks", "test (ubuntu-24.04)", "test (windows-latest)", "windows-source"}


def text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def load(name):
    return yaml.safe_load(text(name))


def triggers(data):
    return data.get("on", data.get(True))  # YAML 1.1 reads a bare `on` as true


def steps(job):
    return {s.get("name") or s.get("uses").split("@")[0]: s for s in job["steps"]}


def runs(data):
    """Every step's script, with where it is."""
    return [(job_id, s.get("name"), s["run"]) for job_id, job in data["jobs"].items() for s in job.get("steps", [])
            if "run" in s]


@unittest.skipIf(yaml is None, "no PyYAML")
class Workflows(unittest.TestCase):
    """Rules every workflow keeps."""

    @classmethod
    def setUpClass(cls):
        cls.names = sorted(p.name for p in WORKFLOWS.glob("*.yml"))
        cls.flows = {name: load(name) for name in cls.names}

    def test_the_workflows(self):
        self.assertEqual(self.names, ["codeql.yml", "desktop.yml", "pages.yml", "preview.yml", "release.yml",
                                      "security.yml", "stats.yml", "tests.yml"])

    def test_every_action_is_pinned_to_a_commit_with_its_version_beside_it(self):
        pins = {}
        for name in self.names:
            for line in text(name).splitlines():
                m = re.match(r"\s*(?:- )?uses: (\S+)(.*)", line)
                if not m or m.group(1).startswith("./"):
                    continue
                action, rest = m.groups()
                if name in UNPINNED:
                    continue
                with self.subTest(name, action=action):
                    self.assertRegex(action, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$", "pinned to a full commit sha")
                    self.assertRegex(rest, r"^ # v\d+\.\d+\.\d+$", "the version it is, in a comment")
                    repo, sha = action.split("@")
                    pins.setdefault(repo.split("/")[0] + "/" + repo.split("/")[1], set()).add((sha, rest))
        for repo, found in pins.items():
            self.assertEqual(len(found), 1, f"{repo} at one version everywhere: {found}")
        self.assertIn("actions/checkout", pins)
        self.assertIn("github/codeql-action", pins)

    def test_permissions_are_the_fewest_each_job_needs(self):
        writes = {}
        for name, data in self.flows.items():
            with self.subTest(name):
                self.assertIn("permissions", data, "every workflow says what its token may do")
            for job_id, perms in [(None, data["permissions"]), *((j, job.get("permissions", {}))
                                                                for j, job in data["jobs"].items())]:
                granted = {k for k, v in (perms or {}).items() if v == "write"}
                if granted:
                    writes[(name, job_id)] = granted
        self.assertEqual(writes, WRITERS)

    def test_no_secrets_for_forks_and_each_secret_in_one_place(self):
        for name in self.names:
            with self.subTest(name):
                self.assertNotIn("pull_request_target", text(name), "a fork's pull request gets no secrets")
                if name == "pages.yml":  # it follows the stats and the Desktop build, never a fork's run
                    self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository",
                                  self.flows[name]["jobs"]["build"]["if"])
                else:
                    self.assertNotIn("workflow_run", text(name))
        found = {name: re.findall(r"secrets\.(\w+)", text(name)) for name in self.names}
        self.assertEqual({k: v for k, v in found.items() if v},
                         {"desktop.yml": ["WINGET_TOKEN", "WINGET_TOKEN"], "release.yml": ["WINGET_TOKEN"],
                          "stats.yml": ["STATS_TOKEN"]})
        self.assertNotIn("secrets: inherit", "".join(text(n) for n in self.names), "only the secret a call needs")

    def test_no_untrusted_text_is_pasted_into_a_script(self):
        for name, data in self.flows.items():
            for job_id, step, script in runs(data):
                with self.subTest(name, job=job_id, step=step):
                    self.assertNotRegex(script, r"\$\{\{\s*(inputs\.|github\.event\.|github\.head_ref)",
                                        "through env, never into the script")

    def test_checkouts_keep_no_token_and_jobs_have_a_time_limit(self):
        for name, data in self.flows.items():
            for job_id, job in data["jobs"].items():
                with self.subTest(name, job=job_id):
                    if "uses" in job:  # a call: the called workflow's jobs have their own limits
                        continue
                    self.assertIn("timeout-minutes", job)
                    for step in job["steps"]:
                        if str(step.get("uses", "")).startswith("actions/checkout@") and name not in UNPINNED:
                            self.assertIs(step.get("with", {}).get("persist-credentials"), False)

    def test_a_new_push_to_a_pull_request_cancels_the_run_it_replaces(self):
        for name in ("tests.yml", "codeql.yml", "security.yml", "desktop.yml"):
            with self.subTest(name):
                concurrency = self.flows[name]["concurrency"]
                self.assertEqual(concurrency["cancel-in-progress"], "${{ github.event_name == 'pull_request' }}")
                self.assertIn("github.event.pull_request.number ||", concurrency["group"])
        # on main every commit keeps its run: the nightly and the Release workflow look for the commit's own
        self.assertEqual(self.flows["tests.yml"]["concurrency"]["group"], "tests-${{ github.event.pull_request.number || github.sha }}")
        for name in ("release.yml", "preview.yml", "pages.yml", "stats.yml"):
            self.assertIs(self.flows[name]["concurrency"]["cancel-in-progress"], False, name)


@unittest.skipIf(yaml is None, "no PyYAML")
class Tests(unittest.TestCase):
    """tests.yml: the quick checks first, then the slower jobs, whose names main requires."""

    @classmethod
    def setUpClass(cls):
        cls.data = load("tests.yml")
        cls.jobs = cls.data["jobs"]

    def test_every_push_to_main_and_every_pull_request(self):
        self.assertEqual(triggers(self.data), {"push": {"branches": ["main"]}, "pull_request": None})
        self.assertEqual(self.data["permissions"], {})
        for job in self.jobs.values():
            self.assertEqual(job["permissions"], {"contents": "read"})

    def test_the_check_names_main_requires(self):
        names = {"checks", "windows-source"} | {f"test ({os})" for os in self.jobs["test"]["strategy"]["matrix"]["os"]}
        self.assertEqual(names, REQUIRED_CHECKS)
        self.assertEqual(set(self.jobs["test"]["strategy"]["matrix"]), {"os"}, "another key would change the names")
        doc = " ".join((ROOT / "docs" / "14-pipeline.md").read_text(encoding="utf-8").split())
        self.assertIn("`checks`, `test (ubuntu-24.04)`, `test (windows-latest)` and `windows-source`", doc)

    def test_the_slower_jobs_wait_for_the_quick_checks(self):
        self.assertNotIn("needs", self.jobs["checks"])
        for name in ("test", "windows-source"):
            self.assertEqual(self.jobs[name]["needs"], "checks")

    def test_the_quick_checks(self):
        job = self.jobs["checks"]
        self.assertLessEqual(job["timeout-minutes"], 10)
        script = "\n".join(s.get("run", "") for s in job["steps"])
        self.assertIn('python -m pip install "$(grep -E \'^ruff==\' requirements-dev.txt)"', script)
        self.assertIn("ruff check --output-format=github .", script)
        self.assertIn("git ls-files -z '*.js' | xargs -0 -n 1 node --check", script)
        self.assertIn("git ls-files -z '*.sh' | xargs -0 shellcheck", script)
        self.assertIn('echo "$ACTIONLINT_SHA256  $RUNNER_TEMP/$archive" | sha256sum -c -', script)
        self.assertIn("python -m unittest tests.test_docs_links tests.test_public_text -v", script)
        lint = steps(job)["Workflows (actionlint)"]["env"]
        self.assertRegex(lint["ACTIONLINT_VERSION"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(lint["ACTIONLINT_SHA256"], r"^[0-9a-f]{64}$")
        # the quick tests need nothing installed: the standard library only
        for test in ("test_docs_links.py", "test_public_text.py"):
            source = (ROOT / "tests" / test).read_text(encoding="utf-8")
            imported = set(re.findall(r"^(?:import|from) (\w+)", source, re.M))
            self.assertLessEqual(imported, set(sys.stdlib_module_names), test)

    def test_ruff_is_pinned_and_looks_for_errors_only(self):
        pins = re.findall(r"^ruff==(\d+\.\d+\.\d+)$", (ROOT / "requirements-dev.txt").read_text(encoding="utf-8"), re.M)
        self.assertEqual(len(pins), 1)
        config = tomllib.loads((ROOT / "ruff.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["lint"]["select"], ["E9", "F"])
        self.assertEqual(config["target-version"], "py312")

    def test_the_suite_writes_a_summary_and_keeps_failed_browser_tests(self):
        job = self.jobs["test"]
        by_name = steps(job)
        step = by_name["Tests (the hosted services are local stand-ins; no models needed)"]
        self.assertEqual(step["run"], "python .github/scripts/run_tests.py ${{ runner.os == 'Linux' && '--coverage' || '' }}")
        self.assertEqual(step["env"], {"SEDJEM_UI_REQUIRED": "1", "SEDJEM_UI_TRACES": "ui-failures"})
        upload = by_name["Upload the failed browser tests' traces and screenshots"]
        self.assertEqual(upload["if"], "failure()")
        self.assertEqual(upload["with"]["path"], "ui-failures")
        self.assertEqual(upload["with"]["if-no-files-found"], "ignore")
        self.assertIn("${{ matrix.os }}", upload["with"]["name"])
        for s in job["steps"]:
            self.assertNotIn("continue-on-error", s, "no step's failure is hidden")
        self.assertNotRegex(str(job["steps"]), r"(?i)retry|retries|rerun", "no automatic retries")
        self.assertEqual(by_name["actions/setup-python"]["with"]["cache"], "pip")


@unittest.skipIf(yaml is None, "no PyYAML")
class DesktopBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load("desktop.yml")
        cls.jobs = cls.data["jobs"]

    def test_the_build_that_publishes_restores_no_cache(self):
        # a cache written by an earlier run could otherwise be restored into a release (actions/cache-poisoning)
        for job in self.jobs.values():
            for s in job.get("steps", []):
                self.assertNotIn("cache", s.get("with", {}), s.get("uses") or s.get("name"))
                self.assertNotIn("actions/cache", s.get("uses", ""))

    def test_pull_requests_that_change_the_build_are_built_and_never_published(self):
        paths = triggers(self.data)["pull_request"]["paths"]
        for path in ("desktop/build.py", "desktop/installer.iss", "requirements.txt", "requirements-desktop.txt",
                     "requirements-dev.txt", "app/__main__.py", "app/desktop.py", "app.sh", "app.cmd",
                     ".github/workflows/tests.yml", ".github/scripts/release.py", ".github/scripts/run_tests.py"):
            with self.subTest(path):
                self.assertTrue((ROOT / path).exists())
                self.assertTrue(any(fnmatch.fnmatch(path, p) for p in paths))
        for path in ("app/server.py", "docs/09-app.md", "site/index.html", "tests/test_app.py"):
            self.assertFalse(any(fnmatch.fnmatch(path, p) for p in paths), path)
        version = steps(self.jobs["version"])["Version"]["run"]
        self.assertIn("else                                      # by hand (even on a tag) or for a pull request: not published\n"
                      "  version=$app publish=false", version)
        for job in ("release", "winget"):
            self.assertIn("needs.version.outputs.", self.jobs[job]["if"])

    def test_a_release_waits_for_the_release_environment(self):
        self.assertEqual(self.jobs["release"]["environment"], "${{ needs.version.outputs.channel == 'stable' && 'release' || '' }}")
        self.assertEqual(self.jobs["winget"]["needs"], ["version", "release"], "after the release, so after its approval")
        call = triggers(self.data)["workflow_call"]
        self.assertEqual(call["secrets"], {"WINGET_TOKEN": {"description": call["secrets"]["WINGET_TOKEN"]["description"],
                                                            "required": False}})

    def test_releases_publish_their_notes_or_a_draft_on_an_existing_tag(self):
        release = self.jobs["release"]
        checkout = steps(release)["actions/checkout"]
        self.assertEqual(checkout["with"]["fetch-depth"], 0, "the tags and the history, for a draft")
        run = steps(release)["Publish"]["run"]
        stable = run[run.index("\nelse\n"):]
        self.assertIn('python3 ../.github/scripts/release.py notes "$VERSION" --commit "$COMMIT"', stable)
        self.assertIn("--verify-tag", stable)
        self.assertNotIn("--generate-notes", stable)

    def test_the_tests_before_the_build_write_a_summary(self):
        by_name = steps(self.jobs["build"])
        step = by_name["Tests (the hosted services are local stand-ins; no models needed)"]
        self.assertIn("python .github/scripts/run_tests.py", step["run"])
        self.assertEqual(step["env"]["SEDJEM_UI_TRACES"], "ui-failures")
        self.assertEqual(by_name["Upload the failed browser tests' traces and screenshots"]["if"], "failure()")

    def test_the_build_updates_the_newest_release_as_update_now_does(self):
        flags = re.search(r"^INSTALLER_FLAGS = (\[.*\])$", (ROOT / "app" / "updates.py").read_text(encoding="utf-8"), re.M)
        flags = ", ".join(f'"{f}"' for f in ast.literal_eval(flags.group(1)))
        names = [s.get("name") for s in self.jobs["build"]["steps"]]
        self.assertLess(names.index("Installer update mode (Windows)"), names.index("Update over the newest release (Windows)"))
        step = steps(self.jobs["build"])["Update over the newest release (Windows)"]
        self.assertEqual(step["if"], "runner.os == 'Windows' && hashFiles('dist/Sedjem-*-setup.exe') != ''")
        run = step["run"]
        for part in ('gh release download --repo $env:GITHUB_REPOSITORY --pattern "*-setup.exe"', f"@({flags})",
                     '"/CURRENTUSER", "/DIR=`"$dir`""', '-ArgumentList "--server"', "$stamp.commit -ne $env:COMMIT",
                     "$running.version -ne $env:VERSION", "unins000.exe", "WaitForExit(300000)"):
            self.assertIn(part, run)
        # setup starts first and Sedjem quits after, as app/updates.py does it
        self.assertLess(run.index(f"Start-Process -FilePath $setup -ArgumentList @({flags})"), run.index('/api/quit"'))
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")


@unittest.skipIf(yaml is None, "no PyYAML")
class Release(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load("release.yml")
        cls.jobs = cls.data["jobs"]

    def test_by_hand_with_the_version(self):
        inputs = triggers(self.data)["workflow_dispatch"]["inputs"]
        self.assertEqual(set(triggers(self.data)), {"workflow_dispatch"})
        self.assertIs(inputs["version"]["required"], True)
        self.assertIs(inputs["commit"]["required"], False)
        self.assertEqual(self.data["concurrency"], {"group": "release", "cancel-in-progress": False})

    def test_check_then_tag_then_the_desktop_build(self):
        check, tag, build = self.jobs["check"], self.jobs["tag"], self.jobs["build"]
        self.assertEqual(check["permissions"], {"contents": "read", "actions": "read"})
        self.assertIn('release.py check "$VERSION" --commit "$(git rev-parse HEAD)"', steps(check)["The version and the commit"]["run"])
        self.assertIn('release.py notes "$VERSION" --dry-run', steps(check)["The release notes"]["run"])
        self.assertEqual(steps(check)["actions/checkout"]["with"]["fetch-depth"], 0)
        self.assertEqual((tag["needs"], tag["permissions"]), ("check", {"contents": "write"}))
        self.assertIn('release.py tag "$VERSION" --commit "$COMMIT"', steps(tag)["Tag the commit"]["run"])
        self.assertEqual(build["needs"], ["check", "tag"])
        self.assertEqual(build["uses"], "./.github/workflows/desktop.yml")
        self.assertEqual(build["with"], {"version": "${{ inputs.version }}", "commit": "${{ needs.check.outputs.commit }}"})
        self.assertEqual(build["secrets"], {"WINGET_TOKEN": "${{ secrets.WINGET_TOKEN }}"})
        self.assertEqual(check["outputs"]["commit"], "${{ steps.check.outputs.commit }}")


@unittest.skipIf(yaml is None, "no PyYAML")
class Security(unittest.TestCase):
    def test_codeql_on_the_python_the_javascript_and_the_workflows(self):
        data = load("codeql.yml")
        self.assertEqual(set(triggers(data)), {"push", "pull_request", "schedule", "workflow_dispatch"})
        job = data["jobs"]["analyze"]
        self.assertEqual(job["strategy"]["matrix"]["language"], ["python", "javascript-typescript", "actions"])
        self.assertEqual(job["permissions"], {"contents": "read", "security-events": "write"})
        init, analyze = (s["uses"] for s in job["steps"] if "codeql-action" in s.get("uses", ""))
        self.assertEqual(init.split("@")[1], analyze.split("@")[1], "init and analyze at the same version")

    def test_pip_audit_and_the_dependency_review(self):
        data = load("security.yml")
        self.assertEqual(set(triggers(data)), {"push", "pull_request", "schedule", "workflow_dispatch"})
        audit = steps(data["jobs"]["pip-audit"])["pip-audit"]["run"]
        self.assertRegex(audit, r"pip install pip-audit==\d+\.\d+\.\d+")
        for name in ("requirements.txt", "requirements-desktop.txt", "requirements-dev.txt"):
            self.assertIn(f"-r {name}", audit)
        review = data["jobs"]["dependency-review"]
        self.assertEqual(review["if"], "github.event_name == 'pull_request'")
        self.assertEqual(review["steps"][0]["with"], {"fail-on-severity": "high"})

    def test_dependabot(self):
        config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
        pip, actions = config["updates"]
        self.assertEqual((pip["package-ecosystem"], actions["package-ecosystem"]), ("pip", "github-actions"))
        for update in (pip, actions):
            self.assertEqual(update["schedule"]["interval"], "weekly")
            self.assertTrue(update["groups"])
        self.assertEqual(pip["versioning-strategy"], "increase-if-necessary")
        self.assertEqual(pip["groups"]["python"]["update-types"], ["minor", "patch"])
        # the engines first, so they never ride along with the routine updates, and every kind of their release
        self.assertEqual(list(pip["groups"]), ["engines", "python"])
        self.assertNotIn("update-types", pip["groups"]["engines"])
        engines = set(pip["groups"]["engines"]["patterns"])
        for name in ("faster-whisper", "av", "sherpa-onnx", "transcribe-cpp"):
            self.assertIn(name, engines)
        self.assertEqual(actions["groups"]["github-actions"]["patterns"], ["*"], "CodeQL's init and analyze together")
        # the PyAV ceiling in requirements.txt is kept
        self.assertIn("av>=11,<19", (ROOT / "requirements.txt").read_text(encoding="utf-8"))
        self.assertEqual(pip["ignore"], [{"dependency-name": "av", "versions": [">=19"]}])
        # titles with an area, as the release notes group them
        self.assertEqual((pip["commit-message"]["prefix"], actions["commit-message"]["prefix"]), ("Dependencies", "Workflows"))

    def test_the_templates(self):
        forms = ROOT / ".github" / "ISSUE_TEMPLATE"
        for name in ("bug.yml", "idea.yml"):
            form = yaml.safe_load((forms / name).read_text(encoding="utf-8"))
            with self.subTest(name):
                self.assertTrue(form["name"] and form["description"] and form["labels"])
                ids = [field["id"] for field in form["body"]]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(any(f.get("validations", {}).get("required") for f in form["body"]))
        config = yaml.safe_load((forms / "config.yml").read_text(encoding="utf-8"))
        self.assertTrue(config["contact_links"][0]["url"].endswith("/security/advisories/new"))
        template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
        for part in ("start with the area", "unittest discover -s tests", "Nothing private", "commercial licence"):
            self.assertIn(part, template)


class RunTests(unittest.TestCase):
    """.github/scripts/run_tests.py: the counts, the failures and the coverage in the summary."""

    @staticmethod
    def example():
        class Example(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                pass

            def test_passes(self):
                pass

            def test_fails(self):
                self.assertEqual(1, 2, "one is not two")

            def test_errs(self):
                raise RuntimeError("broken")

            @unittest.skip("not here")
            def test_skipped(self):
                pass

            def test_two_subtests_fail(self):
                for i in range(3):
                    with self.subTest(i=i):
                        self.assertEqual(i, 0)

            @unittest.expectedFailure
            def test_known_to_fail(self):
                self.fail()

        class Broken(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("no browser")

            def test_never_runs(self):
                pass

        load = unittest.defaultTestLoader.loadTestsFromTestCase
        return unittest.TestSuite([load(Example), load(Broken)])

    def test_the_counts_and_the_failures(self):
        result, seconds = rt.run(self.example(), io.StringIO())
        self.assertEqual(rt.counts(result), {"passed": 1, "failed": 2, "errors": 2, "skipped": 1})
        text = rt.summary(result, 75.4, "Tests on Linux")
        self.assertTrue(text.startswith("### Tests on Linux: failed\n"))
        self.assertIn("| 1 | 2 | 2 | 1 | 1 min 15 s |", text)
        self.assertIn("one is not two", text)
        self.assertIn("RuntimeError: broken", text)
        self.assertIn("RuntimeError: no browser", text)
        self.assertIn("(i=1)", text)
        self.assertIn(": not here", text)
        self.assertIn("The slowest tests", text)
        self.assertNotIn("Coverage", text)
        self.assertNotIn("**", text)

    def test_a_run_that_passed(self):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(type("Fine", (unittest.TestCase,), {"test_a": lambda self: None}))
        result, _ = rt.run(suite, io.StringIO())
        text = rt.summary(result, 3, coverage=(61.4, "| Name | Stmts |\n|---|---|\n| app/x.py | 3 |\n"))
        self.assertTrue(text.startswith("### Tests: passed\n"))
        self.assertIn("| 1 | 0 | 0 | 0 | 3 s |", text)
        self.assertIn("Coverage: 61% of the statements ran (measured, not enforced).", text)
        self.assertIn("| app/x.py | 3 |", text)

    def test_the_script_with_coverage_and_its_exit_status(self):
        try:
            import coverage  # noqa: F401
        except ImportError:
            self.skipTest("coverage.py is not installed (requirements-dev.txt)")
        with tempfile.TemporaryDirectory() as tmp:
            tests = Path(tmp) / "tests"
            tests.mkdir()
            (tests / "test_example.py").write_text(
                "import sys, unittest\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                "class Example(unittest.TestCase):\n"
                "    def test_the_version(self):\n"
                "        import app\n"
                "        self.assertTrue(app.__version__)\n"
                "    def test_code_without_a_file(self):  # as tests/test_windows_release.py reads version-info.txt\n"
                f"        self.assertEqual(eval(compile('1 + 1', {str(ROOT / 'made-up.txt')!r}, 'eval')), 2)\n",
                encoding="utf-8")
            summary = Path(tmp) / "summary.md"
            command = [sys.executable, str(ROOT / ".github" / "scripts" / "run_tests.py"), "--tests", str(tests),
                       "--summary", str(summary), "--coverage", "--title", "Example"]
            done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("### Example: passed", text)
            self.assertRegex(text, r"Coverage: \d+% of the statements ran")
            # the report is Markdown, which escapes "_" and, on Windows, the backslash in a path too
            self.assertIn("app/__init__.py", re.sub(r"\\(.)", r"\1", text).replace("\\", "/"))
            (tests / "test_example.py").write_text("import unittest\nclass E(unittest.TestCase):\n"
                                                    "    def test_x(self):\n        self.fail('no')\n", encoding="utf-8")
            done = subprocess.run([*command[:6], "--title", "Again"], cwd=ROOT, capture_output=True, text=True,
                                  timeout=120)
            self.assertEqual(done.returncode, 1)
            self.assertIn("### Again: failed", summary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
