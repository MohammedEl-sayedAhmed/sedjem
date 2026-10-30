"""Tests for the pipeline (docs/14-pipeline.md): the workflows' triggers, jobs, permissions and secrets, the pinned
actions, the locks every install comes from and the downloads checked before they run, the quick checks that run
before the slower jobs, Dependabot, the issue and pull request templates, and .github/scripts/run_tests.py, which
runs the suite in CI and writes its summary. The workflow checks need PyYAML, as in tests/test_preview_plan.py; the
rest is offline, standard library only (packaging, when it is there, checks the version ranges in the locks).
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
spec = importlib.util.spec_from_file_location("locks", ROOT / ".github" / "scripts" / "locks.py")
assert spec and spec.loader
locks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locks)

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
LOCKS = ".github/locks"
# Packages that come only as source code, so that installing them builds them: pip with --no-build-isolation after
# setuptools.txt, uv with the hashes of the build's setuptools, never a build backend fetched unchecked
BUILT = {"proxy-tools"}
# Every step that downloads a file, and why what it downloads can be trusted: a program it runs is first checked
# against its SHA-256 ("sha256"), or is this repository's own file at the run's commit ("this commit"); "data" is
# read and never run
DOWNLOADS = {
    ("tests.yml", "Workflows (actionlint)"): "sha256",
    ("desktop.yml", "Installer (Windows, Inno Setup)"): "sha256",
    ("desktop.yml", "Update over the newest release (Windows)"): "sha256",
    ("desktop.yml", "Send the release to winget"): "sha256",
    ("preview.yml", "Is there something new to build?"): "this commit",
    ("preview.yml", "Remove previews older than a week"): "this commit",
    ("stats.yml", "Update the stats branch"): "this commit",
    ("pages.yml", "Download the stats branch's numbers (through the API, not a checkout)"): "data",
    ("pages.yml", "Download the latest release's details (through the API)"): "data",
}
DOWNLOAD = re.compile(r"\bcurl |\bwget |Invoke-WebRequest|\biwr |gh release download|"
                      r"application/(?:vnd\.github\.raw|octet-stream)")
# Installers other than pip and uv: nothing is installed with them
OTHER_INSTALLERS = re.compile(r"\b(?:choco|winget|apt|apt-get|brew|npm|pnpm|yarn|pipx|gem|cargo|go) "
                              r"(?:install|ci|add)\b|\bnpx |\buvx |\buv tool |Install-Module|Install-Package")


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


def commands(script):
    """A script's commands, one a line, with the lines it continues (\\ in bash, ` in PowerShell) joined."""
    return re.sub(r"[ \t]*[\\`]\r?\n[ \t]*", " ", script).splitlines()


def lock_packages(name):
    """A lock's packages: {canonical name: version}."""
    text = (ROOT / LOCKS / name).read_text(encoding="utf-8")
    return {locks.canonical(n): v for n, v, _, _ in locks.packages(text)}


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

    def test_every_install_is_from_a_lock_with_hashes(self):
        """pip and uv install only from the locks, every file checked against its hashes; nothing is upgraded
        (the runner's pip stays as it is, or comes from a lock); a package built from source is built with the
        locked setuptools."""
        allowed = {"--require-hashes", "--no-build-isolation", "--require-build-hashes"}
        takes = {"-r": "lock", "--build-constraints": "build", "--python": "venv"}
        installs = 0
        for name, data in self.flows.items():
            for job_id, step, script in runs(data):
                done = []  # the locks this script installed so far, with the program that installed them
                for command in commands(script):
                    m = re.search(r"(\S+(?: -m pip| pip)?) install\b(.*)", command)
                    if not m or not re.search(r"\b(?:pip|uv pip) install\b", command):
                        continue
                    installs += 1
                    program, words = m.group(1), m.group(2).split()
                    with self.subTest(name, job=job_id, step=step, command=command):
                        args, i = {}, 0
                        while i < len(words):
                            word = words[i]
                            if word in takes and i + 1 < len(words):
                                args.setdefault(takes[word], []).append(words[i + 1])
                                i += 2
                                continue
                            self.assertIn(word, allowed, "only a lock, with its hashes; nothing upgraded")
                            i += 1
                        self.assertIn("--require-hashes", words)
                        self.assertEqual(len(args.get("lock", [])), 1, "one lock")
                        lock = args["lock"][0]
                        self.assertRegex(lock, rf"^{re.escape(LOCKS)}/[a-z]+\.txt$")
                        self.assertTrue((ROOT / lock).is_file(), lock)
                        if BUILT & set(lock_packages(lock.split("/")[-1])):
                            if program.endswith("uv pip") or command.lstrip().startswith("uv pip"):
                                self.assertIn("--require-build-hashes", words)
                                self.assertEqual(args.get("build"), [f"{LOCKS}/setuptools.txt"])
                            else:
                                self.assertIn("--no-build-isolation", words)
                                self.assertIn((program, f"{LOCKS}/setuptools.txt"), done, "setuptools first")
                        done.append((program, lock))
        self.assertGreaterEqual(installs, 9)

    def test_no_other_installer_and_no_upgrade(self):
        for name in self.names:
            for job_id, step, script in runs(self.flows[name]):
                with self.subTest(name, job=job_id, step=step):
                    self.assertNotRegex(script, OTHER_INSTALLERS)
                    self.assertNotRegex(script, r"--upgrade\b|\bpip install -U\b|ensurepip")

    def test_every_download_that_runs_is_checked_first(self):
        found = {}
        for name, data in self.flows.items():
            for job_id, step, script in runs(data):
                if DOWNLOAD.search(script):
                    found[(name, step)] = script
        self.assertEqual(set(found), set(DOWNLOADS), "a new download needs its row in DOWNLOADS, and its check")
        for (name, step), kind in DOWNLOADS.items():
            script = found[(name, step)]
            env = next(s.get("env", {}) for j in self.flows[name]["jobs"].values() for s in j.get("steps", [])
                       if s.get("name") == step)
            with self.subTest(name, step=step, kind=kind):
                if kind == "sha256":
                    self.assertRegex(script, r"sha256sum -c|Get-FileHash .+ -Algorithm SHA256")
                    pinned = re.findall(r"\b[0-9a-fA-F]{64}\b", script + " ".join(map(str, env.values())))
                    self.assertTrue(pinned or "SHA256SUMS.txt" in script, "a fixed SHA-256, or the release's sums")
                elif kind == "this commit":
                    self.assertIn("ref=$GITHUB_SHA", script)


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
        self.assertIn("python .github/scripts/locks.py check", script)
        self.assertLess(script.index("locks.py check"), script.index("pip install"), "the locks before anything else")
        self.assertIn("python -m pip install --require-hashes -r .github/locks/tools.txt\nruff check", script)
        self.assertIn("ruff check --output-format=github .", script)
        self.assertIn("git ls-files -z '*.js' | xargs -0 -n 1 node --check", script)
        self.assertIn("git ls-files -z '*.sh' | xargs -0 shellcheck", script)
        self.assertIn('echo "$ACTIONLINT_SHA256  $RUNNER_TEMP/$archive" | sha256sum -c -', script)
        self.assertIn("python -m unittest tests.test_docs_links tests.test_public_text -v", script)
        lint = steps(job)["Workflows (actionlint)"]["env"]
        self.assertRegex(lint["ACTIONLINT_VERSION"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(lint["ACTIONLINT_SHA256"], r"^[0-9a-f]{64}$")
        # the quick tests and the locks' check need nothing installed: the standard library only
        for test in ("tests/test_docs_links.py", "tests/test_public_text.py", ".github/scripts/locks.py"):
            source = (ROOT / test).read_text(encoding="utf-8")
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
                     "requirements-dev.txt", ".github/locks/ci.txt", ".github/locks/setuptools.txt",
                     "app/__main__.py", "app/desktop.py", "app.sh", "app.cmd",
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
        # every lock, as the pins of every system: the markers would leave out Windows' packages on Linux
        self.assertIn("python -m pip install --require-hashes -r .github/locks/tools.txt", audit)
        self.assertIn("for lock in .github/locks/*.txt; do", audit)
        self.assertIn('python .github/scripts/locks.py pins "$lock" > "$RUNNER_TEMP/pins.txt"', audit)
        self.assertIn('pip-audit --progress-spinner off --desc on --disable-pip --no-deps -r "$RUNNER_TEMP/pins.txt" '
                      '|| status=1', audit)
        self.assertTrue(audit.rstrip().endswith('exit "$status"'), "one lock's findings don't hide the others'")
        self.assertRegex((ROOT / LOCKS / "tools.in").read_text(encoding="utf-8"), r"(?m)^pip-audit==\d+\.\d+\.\d+ ")
        self.assertIn("pip-audit", lock_packages("tools.txt"))
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


class Locks(unittest.TestCase):
    """.github/locks/: every package the workflows install, pinned with its hashes, made by .github/scripts/locks.py
    from the requirement files."""

    def test_made_from_the_requirement_files_as_they_are(self):
        self.assertEqual(locks.check(), [], "python .github/scripts/locks.py, then commit .github/locks/")
        self.assertEqual(sorted(p.name for p in (ROOT / LOCKS).iterdir()),
                         sorted([name for name, _, _ in locks.LOCKS] + ["tools.in"]))

    def test_the_check_finds_a_change_without_new_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / LOCKS).mkdir(parents=True)
            for path in [*ROOT.glob("requirements*.txt"), *(ROOT / LOCKS).iterdir()]:
                (root / path.relative_to(ROOT)).write_bytes(path.read_bytes())
            self.assertEqual(locks.check(root), [])
            requirements = root / "requirements.txt"
            text = requirements.read_text(encoding="utf-8")
            requirements.write_text(text.replace("numpy==", "numpy>=").replace("# caps", "# limits"), encoding="utf-8")
            self.assertEqual([p.split()[0] for p in locks.check(root)], [f"{LOCKS}/ci.txt", f"{LOCKS}/source.txt"])
            # comments and spacing don't count
            requirements.write_text(text.replace("# caps", "#   limits").replace("==", " == ", 1), encoding="utf-8")
            self.assertEqual(locks.check(root), [])
            requirements.write_text(text, encoding="utf-8")
            # a lock changed by hand, and the locks made with its versions after it
            ci = root / LOCKS / "ci.txt"
            ci.write_text(ci.read_text(encoding="utf-8").replace("--hash=sha256:", "--hash=sha256:0", 1)[:-1],
                          encoding="utf-8")
            self.assertEqual([p.split()[0] for p in locks.check(root)],
                             [f"{LOCKS}/ci.txt", f"{LOCKS}/source.txt", f"{LOCKS}/setuptools.txt"])
            (root / LOCKS / "ci.txt").unlink()
            (root / LOCKS / "notes.txt").write_text("", encoding="utf-8")
            self.assertIn(f"{LOCKS}/ci.txt is missing", locks.check(root))
            self.assertIn(f"{LOCKS}/notes.txt isn't a lock or tools.in", locks.check(root))

    def test_every_package_is_pinned_with_its_hashes(self):
        for name, _, _ in locks.LOCKS:
            text = (ROOT / LOCKS / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertTrue(text.startswith("# This file was autogenerated by uv via the following command:\n"
                                                f"#    {locks.COMMAND}\n# Made from "))
                found = locks.packages(text)
                self.assertTrue(found)
                for package, version, _, hashes in found:
                    self.assertTrue(hashes, package)
                    self.assertRegex(version, r"^[0-9][0-9A-Za-z.+!-]*$", package)
                self.assertEqual(len({locks.canonical(p) for p, _, _, _ in found}), len(found), "each package once")
                for line in text.splitlines():  # nothing else: no index, link, path or editable install
                    s = line.strip()
                    self.assertTrue(not s or s.startswith("#") or locks.HASH.fullmatch(s.rstrip(" \\"))
                                    or locks.PACKAGE.fullmatch(s), line)

    def test_the_requirements_are_in_their_locks(self):
        try:
            from packaging.specifiers import SpecifierSet
        except ImportError:  # the exact pins are still compared
            SpecifierSet = None
        for name, inputs, constraints in locks.LOCKS:
            pinned = lock_packages(name)
            for path in inputs or []:
                for line in locks.requirement_lines((ROOT / path).read_text(encoding="utf-8")):
                    m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)([^;]*)", line)
                    with self.subTest(name, requirement=line):
                        self.assertTrue(m, "a package and its versions, nothing else")
                        package, versions = locks.canonical(m.group(1)), m.group(2)
                        self.assertIn(package, pinned)
                        if re.fullmatch(r"==[^,*]+", versions):
                            self.assertEqual(pinned[package], versions[2:])
                        elif versions and SpecifierSet:
                            self.assertIn(pinned[package], SpecifierSet(versions))
        # the other locks have ci.txt's versions, and the tools' ruff is requirements-dev.txt's
        ci = lock_packages("ci.txt")
        for name in ("source.txt", "setuptools.txt"):
            for package, version in lock_packages(name).items():
                self.assertEqual(ci.get(package), version, f"{name}: {package}")
        ruff = re.search(r"^ruff==(\S+)$", (ROOT / "requirements-dev.txt").read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual(lock_packages("tools.txt")["ruff"], ruff)
        self.assertEqual(set(lock_packages("setuptools.txt")), {"setuptools"})
        self.assertLessEqual({"ruff", "uv", "pip-audit"}, set(lock_packages("tools.txt")))

    def test_both_systems_and_their_packages(self):
        markers = {m for _, _, m, _ in locks.packages((ROOT / LOCKS / "ci.txt").read_text(encoding="utf-8")) if m}
        self.assertIn("sys_platform == 'win32'", markers, "Windows' own packages, such as pywin32")
        ci = lock_packages("ci.txt")
        for package in ("pywin32", "pythonnet", "numpy", "sherpa-onnx-core", *BUILT):
            self.assertIn(package, ci)
        out = subprocess.run([sys.executable, str(ROOT / ".github" / "scripts" / "locks.py"), "pins",
                              f"{LOCKS}/ci.txt"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(out.stdout.splitlines()), len(ci), "every system's packages, for pip-audit")
        self.assertIn("pywin32==", out.stdout)
        self.assertNotIn(";", out.stdout)

    def test_what_each_system_needs_is_added(self):
        """make() resolves the inputs for each system with the universal lock's versions, and adds what the
        universal resolution missed, as sherpa-onnx's wheels need sherpa-onnx-core."""
        self.assertEqual(locks.resolution("sherpa-onnx-core==1.13.8\n    # via\n    #   -c .github/locks/ci.txt\n"
                                          "    #   sherpa-onnx\nnumpy==2.5.3\n    # via -r requirements.txt\n"),
                         {"sherpa-onnx-core": ("1.13.8", ["sherpa-onnx"]), "numpy": ("2.5.3", [])})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / LOCKS).mkdir(parents=True)
            (root / LOCKS / "x.txt").write_text("# header\nnumpy==2.5.3 \\\n    --hash=sha256:" + "a" * 64 + "\n"
                                                "    # via -r requirements.txt\n", encoding="utf-8")
            answers = {"x86_64-manylinux_2_39": "numpy==2.5.3\nsherpa-onnx-core==1.13.8\n    # via sherpa-onnx\n",
                       "x86_64-pc-windows-msvc": "numpy==2.5.3\nsherpa-onnx-core==1.13.8\n    # via sherpa-onnx\n"
                                                 "pywin32==312\n    # via mcp\n"}
            real = locks.compile_lock
            locks.compile_lock = lambda uv, name, inputs, constraints, extra, flags, root, system=None: answers[system]
            try:
                extra = locks.missing([], "x.txt", ["requirements.txt"], [], root)
            finally:
                locks.compile_lock = real
            self.assertEqual(extra, {"sherpa-onnx-core==1.13.8": ["sherpa-onnx"],
                                     "pywin32==312 ; sys_platform == 'win32'": ["mcp"]})
            locks.annotate("x.txt", {"numpy==2.5.3": ["faster-whisper"]}, root)
            self.assertIn("    # via -r requirements.txt\n    # via faster-whisper (the universal resolution missed it",
                          (root / LOCKS / "x.txt").read_text(encoding="utf-8"))


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
