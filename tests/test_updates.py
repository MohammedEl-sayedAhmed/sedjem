"""Tests for updates (app/updates.py): versions and their order; what runs here (a desktop build's stamp, a
checkout's commit read from .git: a plain one, a worktree, packed refs); every check on both channels, for a git
checkout and for desktop builds, against a stand-in for GitHub's API (tests/github_support.py); skipping; the
background check and the setting it follows; Update now in throwaway git repositories with a local remote
(fast-forward, local changes, another branch, diverged, changed requirements with uv or pip); downloads checked
against SHA256SUMS.txt; which addresses may be downloaded; the restart; and the HTTP API.

Nothing here contacts GitHub: every check goes to the stand-in on 127.0.0.1. git runs only in temporary
repositories, and starting processes (the restarted app, the installer, uv and pip) is replaced by mocks.
Run: .venv/bin/python -m unittest tests.test_updates -v
"""
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import __version__, engines, updates  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402
from github_support import REPO, FakeGitHub  # noqa: E402

HAS_GIT = shutil.which("git") is not None
WHICH = shutil.which
SETUP = b"MZ a stand-in for the installer " * 2000
FILES = {"app.txt": "one\n", "requirements.txt": "requests==2.34.2\n",
         "requirements-desktop.txt": "sedjem-no-such-package==1.0  # not installed here\n",
         "requirements-dev.txt": "# for the tests\nnumpy\n"}
A, B, C = "a" * 40, "b" * 40, "c" * 40


def rmtree(path):
    def writable(fn, p, _exc):  # git's object files are read-only, which Windows won't delete
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    shutil.rmtree(path, onexc=writable)


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    if p.returncode:
        raise AssertionError(f"git {' '.join(args)}: {p.stderr}")
    return p.stdout.strip()


def git_env(folder):
    """git as these tests want it, whatever the computer's own settings (hooks, signing, line endings)."""
    empty = Path(folder) / "gitconfig"
    empty.write_text("", encoding="utf-8")
    return {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Sedjem Tests",
            "GIT_AUTHOR_EMAIL": "tests@example.com", "GIT_COMMITTER_NAME": "Sedjem Tests",
            "GIT_COMMITTER_EMAIL": "tests@example.com"}


class Repos:
    """A bare repository standing in for the one on GitHub, a clone that pushes new commits to it (the work on
    main), and the checkout Sedjem runs from, cloned from it too."""

    def __init__(self, folder):
        self.remote, self.work, self.checkout = folder / "remote.git", folder / "work", folder / "checkout"
        git(folder, "init", "-q", "--bare", "-b", "main", str(self.remote))
        git(folder, "clone", "-q", str(self.remote), str(self.work))
        self.first = self.commit(FILES, "First")
        git(folder, "clone", "-q", str(self.remote), str(self.checkout))

    def commit(self, files, message, where=None, push=True):
        """Commit files ({name: text}) in the work clone and push it to main; in another clone, or with push
        False, only commit."""
        where = where or self.work
        for name, text in files.items():
            (where / name).write_text(text, encoding="utf-8")
        git(where, "add", "-A")
        git(where, "commit", "-q", "-m", message)
        if push and where == self.work:
            git(where, "push", "-q", "origin", "HEAD:main")
        return git(where, "rev-parse", "HEAD")


def tar_gz(members):
    """A .tar.gz in memory from {name: bytes, None for a folder, or ("link", target) for a symbolic link}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            if isinstance(data, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, data[1]
                tar.addfile(info)
            elif data is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            else:
                info.size, info.mode = len(data), 0o755
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def linux_build(version):
    return tar_gz({"Sedjem": None, "Sedjem/Sedjem": b"#!/bin/sh\n", "Sedjem/_internal": None,
                   "Sedjem/_internal/app": None,
                   "Sedjem/_internal/app/_build.json": json.dumps({"version": version, "channel": "stable"}).encode()})


# ---------------------------------------------------------------------------------------------
# Versions and settings
# ---------------------------------------------------------------------------------------------

class VersionTests(unittest.TestCase):
    def test_releases_and_previews_are_read(self):
        self.assertEqual(updates.parse_version("0.2.0"), (0, 2, 0, 1, 0, 0))
        self.assertEqual(updates.parse_version("v0.3.0-preview.20261021"), (0, 3, 0, 0, 20261021, 0))
        self.assertEqual(updates.parse_version("0.3.0-preview.20261021.2"), (0, 3, 0, 0, 20261021, 2))
        for bad in ("", " ", "v", "0.2", "0.2.0.1", "0.2.0-beta", "0.2.0-preview", "0.2.0-preview.2026102",
                    "0.2.0-preview.20261021.", "x0.2.0", "main", None, 3, ["0.2.0"]):
            self.assertIsNone(updates.parse_version(bad), bad)

    def test_a_preview_comes_after_the_release_before_it_and_before_its_own(self):
        order = ["0.1.9", "0.2.0", "0.2.1-preview.20261021", "0.2.1-preview.20261021.2", "0.2.1-preview.20261021.10",
                 "0.2.1-preview.20261022", "0.2.1", "0.3.0-preview.20261101", "0.3.0", "0.10.0", "1.0.0"]
        self.assertEqual(sorted(reversed(order), key=updates.parse_version), order)
        self.assertTrue(updates.newer("0.3.0", "0.3.0-preview.20261021"))
        self.assertTrue(updates.newer("v0.2.1-preview.20261021", "0.2.0"))
        self.assertFalse(updates.newer("0.2.0", "0.2.0"))
        self.assertFalse(updates.newer("0.3.0", "not a version"))

    def test_the_settings_are_checked_when_saved(self):
        home = Path(tempfile.mkdtemp(prefix="sedjem-updates-set-")).resolve()
        self.addCleanup(rmtree, home)
        (home / "app_data").mkdir()
        cfg = Config(home=home)
        s = cfg.settings()
        self.assertEqual((s["update_check"], s["update_channel"], s["update_skip"]), (None, "stable", None))
        cfg.save_settings(update_check=True, update_channel="latest", update_skip="0.3.0")
        cfg.save_settings(update_skip=B)
        again = Config(home=home).settings()
        self.assertEqual((again["update_check"], again["update_channel"], again["update_skip"]), (True, "latest", B))
        cfg.save_settings(update_check=None, update_skip=None)  # back to "not asked yet", nothing skipped
        for bad in ({"update_check": "yes"}, {"update_check": 1}, {"update_channel": "nightly"},
                    {"update_channel": None}, {"update_skip": "latest"}, {"update_skip": 7}, {"update_skip": ""}):
            with self.assertRaises(ValueError, msg=bad):
                cfg.save_settings(**bad)
        self.assertEqual(cfg.settings()["update_channel"], "latest")


# ---------------------------------------------------------------------------------------------
# What runs here
# ---------------------------------------------------------------------------------------------

class RunningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-running-")).resolve()  # (Windows: the long name, not RUNNER~1)
        self.addCleanup(rmtree, self.tmp)

    def stamp(self, data):
        path = self.tmp / "_build.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_a_desktop_build_reads_its_stamp(self):
        stamp = self.stamp({"version": "0.2.1-preview.20261021", "commit": C, "date": "2026-10-19T20:02:40Z",
                            "channel": "preview", "built": "2026-10-21T02:00:00Z"})
        exe = self.tmp / "Sedjem" / "Sedjem"
        exe.parent.mkdir()
        run = updates.running(frozen=True, stamp=stamp, executable=exe, platform="linux")
        self.assertEqual(run, {"version": "0.2.1-preview.20261021", "commit": C, "date": "2026-10-19T20:02:40Z",
                               "kind": "linux", "build_channel": "preview", "branch": None})

    def test_the_kind_of_desktop_build(self):
        stamp = self.stamp({"version": "0.3.0", "commit": None, "channel": "stable"})
        exe = self.tmp / "Sedjem" / "Sedjem.exe"
        exe.parent.mkdir()
        kind = lambda platform: updates.running(frozen=True, stamp=stamp, executable=exe, platform=platform)["kind"]
        self.assertEqual(kind("win32"), "other", "a copy the installer didn't put there")
        (exe.parent / "unins000.exe").write_bytes(b"")
        self.assertEqual(kind("win32"), "windows")
        self.assertEqual(kind("linux"), "linux")
        self.assertEqual(kind("darwin"), "other")

    def test_a_build_without_a_stamp_or_with_a_broken_one(self):
        for stamp in (self.tmp / "missing.json", self.stamp({"version": "soon", "commit": "abc", "channel": "beta"})):
            run = updates.running(frozen=True, stamp=stamp, executable=self.tmp / "Sedjem", platform="linux")
            self.assertEqual((run["version"], run["commit"], run["date"], run["build_channel"]),
                             (__version__, None, None, None), stamp)

    def fake_git(self, folder, head, refs=None, packed=None):
        """A .git folder written by hand: HEAD, loose refs, packed-refs."""
        dot = folder / ".git"
        (dot / "refs" / "heads").mkdir(parents=True)
        (dot / "HEAD").write_text(head + "\n", encoding="utf-8")
        for ref, sha in (refs or {}).items():
            (dot / ref).parent.mkdir(parents=True, exist_ok=True)
            (dot / ref).write_text(sha + "\n", encoding="utf-8")
        if packed:
            (dot / "packed-refs").write_text(packed, encoding="utf-8")
        return dot

    def test_a_checkout_without_git_installed(self):
        with mock.patch.object(updates.shutil, "which", return_value=None):
            self.fake_git(self.tmp / "loose", "ref: refs/heads/main", {"refs/heads/main": A})
            self.assertEqual(updates.read_head(self.tmp / "loose"), (A, "main"))
            packed = "# pack-refs with: peeled fully-peeled sorted \n" \
                     f"{B} refs/heads/main\n{C} refs/tags/v0.2.0\n^{A}\n{A} refs/remotes/origin/main\n"
            self.fake_git(self.tmp / "packed", "ref: refs/heads/main", packed=packed)
            self.assertEqual(updates.read_head(self.tmp / "packed"), (B, "main"))
            self.fake_git(self.tmp / "detached", C)
            self.assertEqual(updates.read_head(self.tmp / "detached"), (C, None))
            self.fake_git(self.tmp / "symbolic", "ref: refs/heads/main",
                          {"refs/heads/main": "ref: refs/heads/trunk", "refs/heads/trunk": A})
            self.assertEqual(updates.read_head(self.tmp / "symbolic"), (A, "main"))
            self.fake_git(self.tmp / "nothing", "ref: refs/heads/main")
            self.assertEqual(updates.read_head(self.tmp / "nothing"), (None, "main"), "a branch with no commit yet")
            self.assertEqual(updates.read_head(self.tmp / "no-checkout"), (None, None))
            run = updates.running(root=self.tmp / "loose", frozen=False)
            self.assertEqual((run["version"], run["commit"], run["kind"], run["branch"], run["build_channel"]),
                             (__version__, A, "source", "main", None))
            self.assertEqual(updates.running(root=self.tmp / "no-checkout", frozen=False)["kind"], "other")

    def test_a_worktree_written_by_hand(self):
        main = self.fake_git(self.tmp / "main", "ref: refs/heads/main", {"refs/heads/main": A, "refs/heads/feature": B})
        own = main / "worktrees" / "wt"
        own.mkdir(parents=True)
        (own / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
        (own / "commondir").write_text("../..\n", encoding="utf-8")
        (self.tmp / "wt").mkdir()
        (self.tmp / "wt" / ".git").write_text(f"gitdir: {own}\n", encoding="utf-8")
        self.assertEqual(updates.read_head(self.tmp / "wt"), (B, "feature"))
        (self.tmp / "wt" / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n", encoding="utf-8")  # relative
        self.assertEqual(updates.read_head(self.tmp / "wt"), (B, "feature"))

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_real_repositories_plain_packed_and_a_worktree(self):
        with mock.patch.dict(os.environ, git_env(self.tmp)):
            repo = self.tmp / "repo"
            git(self.tmp, "init", "-q", "-b", "main", str(repo))
            (repo / "a.txt").write_text("a\n", encoding="utf-8")
            git(repo, "add", "-A")
            with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": "2026-10-20T10:44:12+0000"}):  # in UTC
                git(repo, "commit", "-q", "-m", "First")
            head = git(repo, "rev-parse", "HEAD")
            self.assertEqual(updates.read_head(repo), (head, "main"))
            date = "2026-10-20T10:44:12Z"  # as git 2.41 and later writes it (earlier ones: +00:00)
            self.assertIn(git(repo, "show", "-s", "--format=%cI", head), (date, "2026-10-20T10:44:12+00:00"))
            with mock.patch.object(updates.shutil, "which", return_value=None):  # from the loose object alone
                self.assertEqual(updates.commit_date(repo, head), date)
            git(repo, "pack-refs", "--all")
            self.assertFalse((repo / ".git" / "refs" / "heads" / "main").exists())
            self.assertEqual(updates.read_head(repo), (head, "main"))
            git(repo, "gc", "-q")  # the commit goes into a pack: its date comes from git now
            self.assertEqual(updates.commit_date(repo, head), date)
            git(repo, "worktree", "add", "-q", "-b", "side", str(self.tmp / "side"))
            (self.tmp / "side" / "b.txt").write_text("b\n", encoding="utf-8")
            git(self.tmp / "side", "add", "-A")
            with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": "2026-10-20T13:50:00+0300"}):  # not in UTC
                git(self.tmp / "side", "commit", "-q", "-m", "Side")
            side = git(self.tmp / "side", "rev-parse", "HEAD")
            self.assertEqual(updates.read_head(self.tmp / "side"), (side, "side"))
            self.assertEqual(updates.read_head(repo), (head, "main"))
            run = updates.running(root=self.tmp / "side", frozen=False)
            self.assertEqual((run["commit"], run["kind"], run["date"]), (side, "source", "2026-10-20T13:50:00+03:00"))


# ---------------------------------------------------------------------------------------------
# Checks, against the stand-in for GitHub
# ---------------------------------------------------------------------------------------------

class UpdatesCase(unittest.TestCase):
    """A stand-in for GitHub, a data folder that points Sedjem at it, and git set up for the tests. Starting
    processes (the restarted app, the installer) and installing requirement files are mocked for every test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-updates-")).resolve()  # (Windows: the long name, not RUNNER~1)
        self.addCleanup(rmtree, self.tmp)
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        home = self.tmp / "home"
        (home / "app_data").mkdir(parents=True)
        (home / "app_data" / "config.toml").write_text(self.gh.config(), encoding="utf-8")
        self.cfg = Config(home=home)
        self.quits, self.shown = [], []
        done = subprocess.CompletedProcess([], 0, "", "")
        for name, patcher in (("env", mock.patch.dict(os.environ, git_env(self.tmp))),
                              ("spawn", mock.patch.object(updates, "spawn")),
                              ("installer", mock.patch.object(updates, "run_installer", return_value=done)),
                              ("gpu", mock.patch.object(engines, "gpu_info", return_value={"devices": []})),
                              ("grace", mock.patch.object(updates, "RESTART_GRACE", 0.3))):
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.spawn.return_value.poll.return_value = None  # the process it starts keeps running

    def make(self, **kw):
        """Updates for this data folder; kw says what runs (root, frozen, stamp, executable, platform)."""
        return updates.Updates(self.cfg, quit=lambda restarting=False: self.quits.append(restarting),
                               show_folder=self.shown.append, port=lambda: 8799, **kw)

    def build(self, kind="linux", version="0.2.0", commit=None, channel=None):
        """Updates for a desktop build in a folder of its own, with its stamp: kind "windows" (installed, with
        its uninstaller), "linux", or "other" (macOS)."""
        folder = self.tmp / "apps" / "Sedjem"
        stamp = folder / "_internal" / "app" / "_build.json"
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({"version": version, "commit": commit, "date": "2026-10-19T20:02:40Z",
                                     "channel": channel or ("preview" if "preview" in version else "stable")}),
                         encoding="utf-8")
        exe = folder / ("Sedjem.exe" if kind == "windows" else "Sedjem")
        exe.write_bytes(b"")
        if kind == "windows":
            (folder / "unins000.exe").write_bytes(b"")
        return self.make(frozen=True, stamp=stamp, executable=exe,
                         platform={"windows": "win32", "linux": "linux", "other": "darwin"}[kind])

    def channel(self, name):
        self.cfg.save_settings(update_channel=name)

    def checked(self, up):
        up.check()
        return up.state()

    def wait_install(self, up, until=("done", "failed", "restarting"), timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            install = up.state(refresh=False)["install"]
            if install and install["state"] in until:
                return install
            time.sleep(0.05)
        self.fail(f"Update now didn't finish: {up.install}")

    def windows_release(self, version="0.3.0", **kw):
        return self.gh.release(version, notes=f"## Sedjem {version}\n- What changed", **kw,
                               files={f"Sedjem-{version}-setup.exe": SETUP,
                                      f"Sedjem-{version}-linux-x64.tar.gz": linux_build(version)})


class BuildChecks(UpdatesCase):
    """Desktop builds (and copies without git): releases, and on Latest also the nightly previews."""

    def test_stable_offers_the_newest_release_above_the_running_version(self):
        self.gh.release("0.1.0")
        self.windows_release("0.3.0")
        self.gh.release("0.2.5")  # published later, but an older version
        st = self.checked(self.build("windows"))
        self.assertIsNone(st["error"])
        self.assertEqual(st["found"], "update")
        self.assertIsNotNone(st["checked"])
        a = st["available"]
        self.assertEqual({k: a[k] for k in ("channel", "version", "title", "notes", "changes", "ahead", "skipped",
                                            "action", "blocked", "commands", "date")},
                         {"channel": "stable", "version": "0.3.0", "title": "Sedjem 0.3.0",
                          "notes": "## Sedjem 0.3.0\n- What changed", "changes": [], "ahead": 0, "skipped": False,
                          "action": "installer", "blocked": None, "commands": [], "date": "2026-10-21T10:00:00Z"})
        self.assertEqual(a["url"], f"https://github.com/{REPO}/releases/tag/v0.3.0")
        self.assertEqual(st["running"], {"version": "0.2.0", "commit": None, "date": "2026-10-19T20:02:40Z",
                                         "kind": "windows", "build_channel": "stable"})

    def test_the_pill(self):
        up = self.build("linux")
        self.assertEqual(up.summary(), {"ask": True, "available": False, "label": None})
        self.windows_release("0.3.0")
        up.check()
        self.assertEqual(up.summary(), {"ask": True, "available": True, "label": "Update available · 0.3.0"})
        self.cfg.save_settings(update_check=False)
        self.assertEqual(up.summary()["ask"], False)

    def test_drafts_and_pre_releases_are_skipped_on_stable(self):
        self.gh.release("0.3.0", draft=True)
        self.gh.release("0.2.1-preview.20261021")
        self.gh.release("0.4.0", prerelease=True)  # a pre-release that isn't a nightly preview
        st = self.checked(self.build("linux"))
        self.assertEqual((st["found"], st["available"], st["error"]), ("up to date", None, None))

    def test_up_to_date(self):
        self.gh.release("0.1.0")
        self.gh.release("0.2.0")
        st = self.checked(self.build("linux"))
        self.assertEqual((st["found"], st["available"], st["error"], st["note"]), ("up to date", None, None, None))
        self.assertEqual(self.gh.paths(), [f"/repos/{REPO}/releases"], "one request")

    def test_latest_offers_the_newest_preview_and_lists_its_changes(self):
        self.gh.release("0.2.1-preview.20261021", commit=B, files={"Sedjem-0.2.1-preview.20261021-linux-x64.tar.gz": b"x"})
        self.gh.release("0.2.1-preview.20261021.2", commit=C,
                        files={"Sedjem-0.2.1-preview.20261021.2-linux-x64.tar.gz": b"x"})
        self.gh.release("0.4.0", prerelease=True)
        self.gh.compares[(A, C)] = {"status": "ahead", "ahead_by": 2, "behind_by": 0, "html_url": "https://github.com/x",
                                    "commits": [{"sha": B, "html_url": f"https://github.com/{REPO}/commit/{B}",
                                                 "commit": {"message": "Older change\n\nWith a body",
                                                            "committer": {"date": "2026-10-20T10:00:00Z"}}},
                                                {"sha": C, "html_url": f"https://github.com/{REPO}/commit/{C}",
                                                 "commit": {"message": "Newer change", "author": {"date": "2026-10-21T10:00:00Z"}}}]}
        self.channel("latest")
        up = self.build("linux", commit=A)
        st = self.checked(up)
        a = st["available"]
        self.assertEqual((a["channel"], a["version"], a["commit"], a["title"], a["ahead"], a["action"], a["blocked"]),
                         ("latest", "0.2.1-preview.20261021.2", C, "2 new changes", 2, "archive", None))
        self.assertEqual(a["changes"], [
            {"sha": C, "title": "Newer change", "date": "2026-10-21T10:00:00Z", "url": f"https://github.com/{REPO}/commit/{C}"},
            {"sha": B, "title": "Older change", "date": "2026-10-20T10:00:00Z", "url": f"https://github.com/{REPO}/commit/{B}"}])
        self.assertEqual(a["url"], f"https://github.com/{REPO}/releases/tag/v0.2.1-preview.20261021.2")
        self.assertEqual(up.summary()["label"], "2 new changes")

    def test_latest_offers_a_release_newer_than_every_preview(self):
        self.gh.release("0.2.1-preview.20261021", commit=B)
        self.windows_release("0.2.1")
        self.channel("latest")
        up = self.build("linux", commit=A)  # GitHub doesn't know A: no list of changes, the version says enough
        a = self.checked(up)["available"]
        self.assertEqual((a["version"], a["title"], a["changes"]), ("0.2.1", "Sedjem 0.2.1", []))
        self.assertEqual(up.summary()["label"], "Update available · 0.2.1")
        self.assertIn(f"/repos/{REPO}/compare/{A}...v0.2.1", self.gh.paths(), "a release's commit is its tag's")

    def test_a_preview_build_gets_its_release_on_stable(self):
        self.windows_release("0.2.1")
        st = self.checked(self.build("linux", version="0.2.1-preview.20261022", commit=C))
        self.assertEqual((st["available"]["version"], st["running"]["build_channel"]), ("0.2.1", "preview"))

    def test_a_copy_that_cant_update_itself_offers_the_download(self):
        self.windows_release("0.3.0")
        up = self.build("other")
        a = self.checked(up)["available"]
        self.assertEqual(a["action"], "download")
        self.assertIn("release page", a["blocked"])
        with self.assertRaises(updates.Refused):
            up.start_install()

    def test_a_release_without_its_files_is_blocked(self):
        self.gh.release("0.3.0")
        up = self.build("windows")
        self.assertEqual(self.checked(up)["available"]["blocked"], "This release has no Sedjem-0.3.0-setup.exe.")
        self.gh.release("0.3.1", files={"Sedjem-0.3.1-setup.exe": SETUP}, sums=False)
        self.assertEqual(self.checked(up)["available"]["blocked"], "This release has no SHA256SUMS.txt to check the download against.")
        with self.assertRaises(updates.Refused):
            up.start_install()

    def test_a_release_whose_files_are_elsewhere_is_blocked(self):
        release = self.windows_release("0.3.0")
        for asset in release["assets"]:
            asset["url"] = asset["browser_download_url"] = f"https://evil.example/{asset['name']}"
        up = self.build("windows")
        self.assertIn("aren't where this repository keeps its releases", self.checked(up)["available"]["blocked"])
        with self.assertRaises(updates.Refused):
            up.start_install()


class CheckErrors(UpdatesCase):
    def test_offline_keeps_what_the_last_check_found(self):
        self.windows_release("0.3.0")
        up = self.build("linux")
        first = self.checked(up)
        self.gh.mode = "offline"
        st = self.checked(up)
        self.assertEqual(st["error"], "offline")
        self.assertEqual(st["checked"], first["checked"], "the time of the last check that worked")
        self.assertEqual(st["available"]["version"], "0.3.0")
        self.assertEqual(up.summary()["label"], "Update available · 0.3.0")
        self.gh.mode = None
        self.assertIsNone(self.checked(up)["error"])

    def test_no_server_at_all_is_offline(self):
        self.gh.close()
        self.assertEqual(self.checked(self.build("linux"))["error"], "offline")

    def test_rate_limited_waits_for_the_reset(self):
        self.windows_release("0.3.0")
        up = self.build("linux")
        self.gh.mode = "rate limited"
        st = self.checked(up)
        self.assertEqual((st["error"], st["available"]), ("rate limited", None))
        self.assertGreater(up.data["rate_reset"], time.time() + 600)
        self.gh.mode, asked = None, len(self.gh.calls)
        self.assertEqual(self.checked(up)["error"], "rate limited")
        self.cfg.save_settings(update_check=True)
        self.assertFalse(up.tick())
        self.assertEqual(len(self.gh.calls), asked, "nothing asked before the reset")
        up.data["rate_reset"] = time.time() - 1
        st = self.checked(up)
        self.assertEqual((st["error"], st["available"]["version"]), (None, "0.3.0"))
        self.assertIsNone(up.data["rate_reset"])

    def test_answers_that_make_no_sense(self):
        for mode in ("malformed", "odd"):
            self.gh.mode = mode
            st = self.checked(self.build("linux"))
            self.assertEqual((st["error"], st["available"]), (updates.ODD, None), mode)

    def test_a_server_error(self):
        self.gh.mode = "error"
        self.assertEqual(self.checked(self.build("linux"))["error"], "GitHub answered with an error (HTTP 502)")

    def test_answers_that_havent_changed_come_from_the_cache(self):
        self.windows_release("0.3.0")
        up = self.build("linux")
        self.checked(up)
        self.assertEqual(self.gh.calls[-1][1:], (None, 200))
        st = self.checked(up)
        self.assertIsNotNone(self.gh.calls[-1][1], "If-None-Match was sent")
        self.assertEqual(self.gh.calls[-1][2], 304)
        self.assertEqual((st["error"], st["available"]["version"]), (None, "0.3.0"))
        again = self.build("linux")  # started again: the ETags were saved in updates.json
        self.assertEqual(self.checked(again)["available"]["version"], "0.3.0")
        self.assertEqual(self.gh.calls[-1][2], 304)
        self.windows_release("0.3.1")
        self.assertEqual(self.checked(again)["available"]["version"], "0.3.1", "a changed answer is read again")
        self.assertEqual(self.gh.calls[-1][2], 200)

    def test_an_unusable_config_asks_nothing(self):
        self.cfg.data["updates"] = {"repo": REPO, "api": "http://example.com"}
        st = self.checked(self.build("linux"))
        self.assertIn("config.toml", st["error"])
        self.assertEqual(self.gh.calls, [])

    def test_a_check_never_raises(self):
        up = self.build("linux")
        with mock.patch.object(up, "find", side_effect=RuntimeError("boom")):
            self.assertEqual(self.checked(up)["error"], "RuntimeError: boom")

    def test_a_check_made_on_the_other_channel_or_before_an_update_isnt_shown(self):
        self.windows_release("0.3.0")
        self.checked(self.build("linux"))
        self.channel("latest")
        st = self.build("linux").state()
        self.assertEqual((st["found"], st["available"], st["checked"]), (None, None, None))
        self.channel("stable")
        self.assertEqual(self.build("linux").state()["available"]["version"], "0.3.0")
        st = self.build("linux", version="0.3.0").state()  # updated since
        self.assertEqual((st["found"], st["available"], st["checked"]), (None, None, None))


class SkipTests(UpdatesCase):
    def test_skipping_hides_the_pill_but_not_the_listing(self):
        self.windows_release("0.3.0")
        up = self.build("linux")
        up.check()
        up.skip(True)
        self.assertEqual(self.cfg.setting("update_skip"), "0.3.0")
        self.assertEqual(up.summary(), {"ask": True, "available": False, "label": None})
        st = up.state()
        self.assertEqual((st["available"]["version"], st["available"]["skipped"]), ("0.3.0", True))
        up.skip(False)
        self.assertIsNone(self.cfg.setting("update_skip"))
        self.assertTrue(up.summary()["available"])
        up.skip(True)
        self.windows_release("0.3.1")
        up.check()
        self.assertEqual(up.summary()["label"], "Update available · 0.3.1", "a newer one isn't skipped")
        self.assertFalse(up.state()["available"]["skipped"])

    def test_nothing_to_skip(self):
        with self.assertRaises(updates.Refused):
            self.build("linux").skip(True)


class BackgroundTests(UpdatesCase):
    def test_it_checks_only_when_the_user_agreed(self):
        self.windows_release("0.3.0")
        up = self.build("linux")
        self.assertFalse(up.tick(), "not asked yet")
        self.cfg.save_settings(update_check=False)
        self.assertFalse(up.tick())
        self.assertEqual(self.gh.calls, [])
        self.cfg.save_settings(update_check=True)
        self.assertTrue(up.tick())
        self.assertEqual(up.state()["available"]["version"], "0.3.0")

    def test_once_a_day_and_an_hour_after_a_failure(self):
        self.cfg.save_settings(update_check=True)
        up = self.build("linux")
        self.assertTrue(up.tick(), "the first one since start")
        last = up.data["attempted"]
        self.assertFalse(up.tick(last + 60))
        self.assertFalse(up.tick(last + updates.EVERY - 60))
        self.assertTrue(up.tick(last + updates.EVERY + 1))
        self.gh.mode = "offline"
        up.data["attempted"] = 0
        self.assertTrue(up.tick())
        self.assertEqual(up.state()["error"], "offline")
        last = up.data["attempted"]
        self.assertFalse(up.tick(last + updates.RETRY - 60))
        self.assertTrue(up.tick(last + updates.RETRY + 1))

    def test_the_thread_checks_soon_after_start_when_allowed(self):
        self.windows_release("0.3.0")
        with mock.patch.object(updates, "START_DELAY", 0.05):
            off = self.build("linux")
            off.start()
            time.sleep(0.6)
            self.assertEqual(self.gh.calls, [], "update_check isn't true")
            self.cfg.save_settings(update_check=True)
            on = self.build("linux")
            on.start()
            end = time.time() + 10
            while on.state()["available"] is None and time.time() < end:
                time.sleep(0.05)
        self.assertEqual(on.state()["available"]["version"], "0.3.0")

    def test_saying_yes_checks_right_away(self):
        self.windows_release("0.3.0")
        with mock.patch.object(updates, "START_DELAY", 3600), mock.patch.object(updates, "WAKE", 3600):
            up = self.build("linux")
            up.start()
            time.sleep(0.2)
            before = self.cfg.settings()
            self.cfg.save_settings(update_check=True)
            up.settings_changed(before, self.cfg.settings())
            end = time.time() + 10
            while up.state()["available"] is None and time.time() < end:
                time.sleep(0.05)
        self.assertEqual(up.state()["available"]["version"], "0.3.0")


# ---------------------------------------------------------------------------------------------
# A git checkout: checks and Update now, with a local remote
# ---------------------------------------------------------------------------------------------

@unittest.skipUnless(HAS_GIT, "needs git")
class SourceTests(UpdatesCase):
    def setUp(self):
        super().setUp()
        self.repos = Repos(self.tmp)
        self.gh.git = self.repos.remote
        self.checkout = self.repos.checkout
        self.channel("latest")
        version = mock.patch.object(updates, "__version__", "0.2.0")  # the checkout's version, below the release
        version.start()
        self.addCleanup(version.stop)

    def up(self):
        return self.make(root=self.checkout, frozen=False)

    def head(self):
        return git(self.checkout, "rev-parse", "HEAD")

    def which(self, uv):
        """shutil.which with uv there (at a made-up path) or not; git stays the real one."""
        return mock.patch.object(updates.shutil, "which", side_effect=lambda name, *a, **kw: (uv if name == "uv" else WHICH(name, *a, **kw)))

    # ---- checks ----------------------------------------------------------------------------------------
    def test_latest_offers_the_newest_tested_commit(self):
        b = self.repos.commit({"app.txt": "two\n"}, "Second change")
        c = self.repos.commit({"app.txt": "three\n"}, "Third change\n\nWith more words below")
        self.gh.run(b)
        self.gh.run(c)
        up = self.up()
        st = self.checked(up)
        self.assertEqual((st["found"], st["error"], st["running"]["commit"], st["running"]["kind"]),
                         ("update", None, self.repos.first, "source"))
        a = st["available"]
        self.assertEqual({k: a[k] for k in ("channel", "version", "commit", "title", "notes", "ahead", "action",
                                            "blocked", "commands", "skipped")},
                         {"channel": "latest", "version": None, "commit": c, "title": "2 new changes", "notes": None,
                          "ahead": 2, "action": "git", "blocked": None, "commands": [], "skipped": False})
        self.assertEqual([(x["sha"], x["title"]) for x in a["changes"]], [(c, "Third change"), (b, "Second change")])
        self.assertEqual(a["url"], f"https://github.com/{REPO}/compare/{self.repos.first}...{c}")
        self.assertEqual(up.summary()["label"], "2 new changes")
        query = next(call[0] for call in self.gh.calls if "/runs" in call[0])
        for part in ("branch=main", "event=push", "status=success", "per_page=1"):
            self.assertIn(part, query)

    def test_a_commit_whose_tests_failed_or_still_run_is_never_offered(self):
        b = self.repos.commit({"app.txt": "two\n"}, "Passed")
        c = self.repos.commit({"app.txt": "three\n"}, "Failed")
        d = self.repos.commit({"app.txt": "four\n"}, "Running")
        self.gh.run(b)
        self.gh.run(c, conclusion="failure")
        self.gh.run(d, status="in_progress")
        self.gh.run("e" * 40, event="pull_request", repo="someone/sedjem")  # a fork's branch that is also called main
        a = self.checked(self.up())["available"]
        self.assertEqual((a["commit"], a["title"], [x["sha"] for x in a["changes"]]), (b, "1 new change", [b]))

    def test_a_fork_pull_request_on_a_branch_called_main_is_ignored(self):
        self.gh.run("e" * 40, event="pull_request", repo="someone/sedjem")
        self.gh.runs[0]["event"] = "push"  # even if the API's filter let it through
        st = self.checked(self.up())
        self.assertEqual((st["found"], st["available"]), ("up to date", None))

    def test_up_to_date_on_the_newest_tested_commit(self):
        self.repos.commit({"app.txt": "two\n"}, "Not tested yet")
        self.gh.run(self.repos.first)
        st = self.checked(self.up())
        self.assertEqual((st["found"], st["available"], st["note"]), ("up to date", None, None))
        self.assertFalse(any("/compare/" in p for p in self.gh.paths()), "nothing to compare")

    def test_a_checkout_ahead_of_the_tested_commit(self):
        self.gh.run(self.repos.first)
        self.repos.commit({"app.txt": "two\n"}, "Not tested yet")
        git(self.checkout, "pull", "-q", "--ff-only")
        st = self.checked(self.up())
        self.assertEqual((st["found"], st["available"]), ("ahead", None))
        self.assertIn("newer commits", st["note"])

    def test_a_diverged_checkout_is_offered_nothing(self):
        b = self.repos.commit({"app.txt": "two\n"}, "On main")
        self.gh.run(b)
        mine = self.repos.commit({"notes.txt": "mine\n"}, "Mine", where=self.checkout)
        git(self.checkout, "push", "-q", "origin", "HEAD:refs/heads/mine")  # GitHub knows the commit, not on main
        up = self.up()
        st = self.checked(up)
        self.assertEqual((st["found"], st["available"], st["running"]["commit"]), ("diverged", None, mine))
        self.assertIn("by hand", st["note"])
        with self.assertRaises(updates.Refused):
            up.start_install()

    def test_a_local_commit_github_doesnt_have(self):
        self.gh.run(self.repos.commit({"app.txt": "two\n"}, "On main"))
        self.repos.commit({"notes.txt": "mine\n"}, "Mine", where=self.checkout)
        st = self.checked(self.up())
        self.assertEqual((st["found"], st["available"]), ("diverged", None))
        self.assertIn("isn't on GitHub", st["note"])

    def test_stable_moves_to_the_release_commit(self):
        b = self.repos.commit({"app.txt": "two\n"}, "Sedjem 0.3.0")
        git(self.repos.work, "tag", "-a", "v0.3.0", "-m", "Sedjem 0.3.0")
        git(self.repos.work, "push", "-q", "origin", "v0.3.0")
        self.repos.commit({"app.txt": "three\n"}, "After the release")
        self.gh.release("0.3.0", notes="Release notes", commit="main")
        self.channel("stable")
        up = self.up()
        a = self.checked(up)["available"]
        self.assertEqual((a["channel"], a["version"], a["commit"], a["title"], a["notes"], a["ahead"], a["action"]),
                         ("stable", "0.3.0", b, "Sedjem 0.3.0", "Release notes", 1, "git"))
        self.assertEqual(up.summary()["label"], "Update available · 0.3.0")
        self.assertIn(f"/repos/{REPO}/commits/v0.3.0", self.gh.paths())
        up.start_install()
        self.assertEqual(self.wait_install(up)["state"], "restarting")
        self.assertEqual(self.head(), b, "the release's commit, not main's newest")

    def test_on_latest_the_commit_is_what_gets_skipped(self):
        c = self.repos.commit({"app.txt": "two\n"}, "Second")
        self.gh.run(c)
        up = self.up()
        up.check()
        up.skip(True)
        self.assertEqual(self.cfg.setting("update_skip"), c)
        self.assertFalse(up.summary()["available"])
        d = self.repos.commit({"app.txt": "three\n"}, "Third")
        self.gh.run(d)
        up.check()
        self.assertEqual(up.summary(), {"ask": True, "available": True, "label": "2 new changes"})

    # ---- Update now ----------------------------------------------------------------------------------------
    def test_update_now_fast_forwards_and_restarts(self):
        self.repos.commit({"app.txt": "two\n"}, "Second")
        c = self.repos.commit({"app.txt": "three\n"}, "Third")
        self.gh.run(c)
        up = self.up()
        self.assertIsNone(self.checked(up)["available"]["blocked"])
        with mock.patch.object(sys, "orig_argv", ["python3", "-m", "app", "--no-browser"]):
            up.start_install()
            install = self.wait_install(up)
            end = time.time() + 10
            while not self.quits and time.time() < end:  # it quits once the new process is seen running
                time.sleep(0.05)
        self.assertEqual(install["state"], "restarting", install)
        self.assertEqual(self.head(), c)
        self.assertEqual((self.checkout / "app.txt").read_text(encoding="utf-8"), "three\n")
        self.installer.assert_not_called()  # no requirement file changed
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0], [sys.executable, "-m", "app", "--no-browser"])
        self.assertEqual(self.spawn.call_args.kwargs["env"][updates.RESTART_ENV], f"{os.getpid()}:8799")
        self.assertEqual(self.spawn.call_args.kwargs["cwd"], os.getcwd())
        self.assertEqual(self.quits, [True])

    def test_local_changes_are_refused(self):
        self.gh.run(self.repos.commit({"app.txt": "two\n"}, "Second"))
        (self.checkout / "app.txt").write_text("my own change\n", encoding="utf-8")
        up = self.up()
        a = self.checked(up)["available"]
        self.assertEqual(a["blocked"], "This checkout has local changes. Commit or stash them first.")
        self.assertEqual(a["commands"][:2], [f'cd "{self.checkout}"', 'git stash push -m "Before updating Sedjem"'])
        self.assertEqual(a["commands"][-1], "git stash pop")
        self.assertIn(f"git merge --ff-only {a['commit']}", a["commands"])
        with self.assertRaises(updates.Refused):
            up.start_install()
        self.assertEqual(self.head(), self.repos.first)
        self.assertEqual((self.checkout / "app.txt").read_text(encoding="utf-8"), "my own change\n")
        git(self.checkout, "checkout", "--", "app.txt")
        (self.checkout / "my-notes.txt").write_text("untracked\n", encoding="utf-8")
        self.assertIsNone(up.state()["available"]["blocked"], "a file git doesn't track isn't a change")
        self.assertEqual(up.state()["available"]["commands"], [])

    def test_another_branch_is_refused(self):
        self.gh.run(self.repos.commit({"app.txt": "two\n"}, "Second"))
        git(self.checkout, "switch", "-q", "-c", "feature")
        up = self.up()
        a = self.checked(up)["available"]
        self.assertEqual(a["blocked"], "This checkout is on the branch feature, not main.")
        self.assertIn("git switch main", a["commands"])
        with self.assertRaises(updates.Refused):
            up.start_install()
        git(self.checkout, "switch", "-q", "--detach", "HEAD")
        self.assertIn("detached", up.state()["available"]["blocked"])

    def test_without_git_the_commands_are_given(self):
        self.gh.run(self.repos.commit({"app.txt": "two\n"}, "Second"))
        up = self.up()
        up.check()
        with mock.patch.object(updates.shutil, "which", return_value=None):
            a = up.state()["available"]
            self.assertIn("Git isn't installed", a["blocked"])
            self.assertIn("git fetch origin main", a["commands"])
            with self.assertRaises(updates.Refused):
                up.start_install()

    def test_a_checkout_that_moved_on_is_refused_when_updating(self):
        b = self.repos.commit({"app.txt": "two\n"}, "On main")
        self.gh.run(b)
        mine = self.repos.commit({"notes.txt": "mine\n"}, "Mine", where=self.checkout)
        self.gh.git = None  # a comparison that says, wrongly, that the checkout is simply behind
        self.gh.compares[(mine, b)] = {"status": "ahead", "ahead_by": 1, "behind_by": 0, "html_url": "",
                                       "commits": [{"sha": b, "commit": {"message": "On main"}}]}
        up = self.up()
        self.assertIsNone(self.checked(up)["available"]["blocked"])
        up.start_install()
        install = self.wait_install(up)
        self.assertEqual(install["state"], "failed")
        self.assertIn("fast-forwarding", install["error"])
        self.assertEqual(self.head(), mine)
        self.spawn.assert_not_called()

    def test_a_commit_that_isnt_on_main_at_origin_is_refused(self):
        git(self.repos.work, "switch", "-q", "-c", "other")
        x = self.repos.commit({"app.txt": "elsewhere\n"}, "Elsewhere", push=False)
        git(self.repos.work, "push", "-q", "origin", "other")
        self.gh.run(x)  # as if the API named a commit that main doesn't have
        up = self.up()
        self.assertEqual(self.checked(up)["available"]["commit"], x)
        up.start_install()
        install = self.wait_install(up)
        self.assertEqual(install["state"], "failed")
        self.assertIn("isn't on main at origin", install["error"])
        self.assertEqual(self.head(), self.repos.first)

    def changed_requirements(self):
        c = self.repos.commit({"requirements.txt": "requests==2.35.0\n", "requirements-dev.txt": "numpy\nmcp\n",
                               "requirements-desktop.txt": "sedjem-no-such-package==2.0\n"}, "New packages")
        self.gh.run(c)
        return c

    def installed_files(self):
        """[(file name, its text then)] the installer was called with."""
        return [(Path(call.args[0][-1]).name, Path(call.args[0][-1]).read_text(encoding="utf-8"))
                for call in self.installer.call_args_list]

    def test_changed_requirements_are_installed_with_uv(self):
        c = self.changed_requirements()
        up = self.up()
        with self.which("/opt/uv/bin/uv"):
            self.assertIsNone(self.checked(up)["available"]["blocked"])
            up.start_install()
            install = self.wait_install(up)
        self.assertEqual(install["state"], "restarting", install)
        self.assertEqual(self.head(), c)
        commands = [call.args[0][:-1] for call in self.installer.call_args_list]
        self.assertEqual(commands, [["/opt/uv/bin/uv", "pip", "install", "-p", sys.executable, "-r"]] * 2)
        # requirements-desktop.txt names nothing installed here, so this environment wasn't set up with it
        self.assertEqual(self.installed_files(), [("requirements-dev.txt", "numpy\nmcp\n"),
                                                  ("requirements.txt", "requests==2.35.0\n")])
        self.assertEqual(self.installer.call_args.kwargs["cwd"], str(self.checkout))

    def test_changed_requirements_are_installed_with_pip_without_uv(self):
        self.changed_requirements()
        up = self.up()
        with self.which(None), mock.patch.object(updates.importlib.util, "find_spec", return_value=object()):
            up.check()
            up.start_install()
            self.assertEqual(self.wait_install(up)["state"], "restarting")
        commands = [call.args[0][:-1] for call in self.installer.call_args_list]
        self.assertEqual(commands, [[sys.executable, "-m", "pip", "install", "-r"]] * 2)

    def test_without_uv_or_pip_the_commands_are_given(self):
        c = self.changed_requirements()
        up = self.up()
        with self.which(None), mock.patch.object(updates.importlib.util, "find_spec", return_value=None):
            a = self.checked(up)["available"]
            self.assertIn("neither uv nor pip", a["blocked"])
            pip = ("& " if sys.platform.startswith("win") else "") + f'"{sys.executable}" -m pip install -r'
            self.assertEqual(a["commands"], [f'cd "{self.checkout}"', "git fetch origin main", f"git merge --ff-only {c}",
                                             f"{pip} requirements-dev.txt", f"{pip} requirements.txt"])
            with self.assertRaises(updates.Refused):
                up.start_install()
        self.assertEqual(self.head(), self.repos.first)

    def test_on_windows_the_commands_are_for_powershell(self):
        # PowerShell, the terminal of VS Code and Windows Terminal, runs a program given by a quoted path only after &
        self.changed_requirements()
        for platform, run in (("win32", "& "), ("linux", "")):
            with self.which(None), mock.patch.object(updates.importlib.util, "find_spec", return_value=None):
                a = self.checked(self.make(root=self.checkout, frozen=False, platform=platform))["available"]
            self.assertIn(f'{run}"{sys.executable}" -m pip install -r requirements.txt', a["commands"], platform)

    def test_with_uv_the_commands_use_it(self):
        self.changed_requirements()
        (self.checkout / "app.txt").write_text("my own change\n", encoding="utf-8")
        with self.which("/opt/uv/bin/uv"):
            a = self.checked(self.up())["available"]
        self.assertIn(f'uv pip install -p "{sys.executable}" -r requirements.txt', a["commands"])

    def test_a_failed_install_leaves_the_checkout_as_it_was(self):
        self.changed_requirements()
        self.installer.return_value = subprocess.CompletedProcess([], 1, "", "ERROR: No matching distribution found")
        up = self.up()
        with self.which("/opt/uv/bin/uv"):
            up.check()
            up.start_install()
            install = self.wait_install(up)
        self.assertEqual(install["state"], "failed")
        self.assertIn("No matching distribution", install["error"])
        self.assertEqual(self.head(), self.repos.first)
        self.spawn.assert_not_called()

    def test_not_while_transcriptions_run(self):
        self.gh.run(self.repos.commit({"app.txt": "two\n"}, "Second"))
        up = self.up()
        up.check()
        with self.assertRaises(updates.Refused) as e:
            up.start_install(jobs_active=True)
        self.assertIn("transcriptions", str(e.exception))


# ---------------------------------------------------------------------------------------------
# Desktop builds: downloads and Update now
# ---------------------------------------------------------------------------------------------

class InstallTests(UpdatesCase):
    def test_windows_downloads_checks_and_runs_the_installer_then_quits(self):
        self.windows_release("0.3.0")
        up = self.build("windows")
        up.check()
        up.start_install()
        install = self.wait_install(up)
        self.assertEqual(install["state"], "restarting", install)
        self.assertEqual((install["done"], install["total"], install["error"]), (len(SETUP), len(SETUP), None))
        setup = self.cfg.storage / "updates" / "Sedjem-0.3.0-setup.exe"
        self.assertEqual(setup.read_bytes(), SETUP)
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0], [str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                                        "/CLOSEAPPLICATIONS", "/UPDATE=1"])
        self.assertEqual(self.quits, [True])
        self.assertIn(f"/repos/{REPO}/releases/assets/", " ".join(self.gh.paths()), "fetched through the API's address")

    def test_a_download_that_doesnt_match_its_checksum_is_deleted(self):
        self.windows_release("0.3.0", bad_sum="Sedjem-0.3.0-setup.exe")
        up = self.build("windows")
        up.check()
        up.start_install()
        install = self.wait_install(up)
        self.assertEqual(install["state"], "failed")
        self.assertIn("doesn't match its checksum", install["error"])
        self.assertEqual(list((self.cfg.storage / "updates").iterdir()), [])
        self.spawn.assert_not_called()
        self.assertEqual(self.quits, [])

    def test_one_update_at_a_time_and_not_while_transcriptions_run(self):
        self.windows_release("0.3.0")
        up = self.build("windows")
        up.check()
        with self.assertRaises(updates.Refused):
            up.start_install(jobs_active=True)
        up.install = {"state": "downloading", "done": 0, "total": None, "error": None, "folder": None}
        with self.assertRaises(updates.Refused) as e:
            up.start_install()
        self.assertIn("already running", str(e.exception))

    def test_nothing_to_install(self):
        with self.assertRaises(updates.Refused):
            self.build("windows").start_install()

    def test_linux_unpacks_the_new_build_beside_the_running_one(self):
        self.windows_release("0.3.0")
        up = self.build("linux")
        up.check()
        up.start_install(jobs_active=True)  # nothing restarts, so running transcriptions don't matter
        install = self.wait_install(up)
        dest = self.tmp / "apps" / "Sedjem-0.3.0"
        self.assertEqual((install["state"], install["folder"], install["error"]), ("done", str(dest), None), install)
        self.assertTrue((dest / "Sedjem").is_file())
        self.assertEqual(json.loads((dest / "_internal" / "app" / "_build.json").read_text())["version"], "0.3.0")
        self.assertEqual(self.shown, [str(dest)])
        self.assertEqual(list((self.cfg.storage / "updates").iterdir()), [], "the archive is deleted once unpacked")
        self.assertEqual(sorted(p.name for p in (self.tmp / "apps").iterdir()), ["Sedjem", "Sedjem-0.3.0"])
        self.assertEqual(self.quits, [])
        up.install = None
        up.start_install()  # again: it is there already
        self.assertEqual(self.wait_install(up)["state"], "done")
        self.assertEqual(self.shown, [str(dest)] * 2)

    def test_linux_refuses_an_archive_that_reaches_outside_its_folder(self):
        for members in ({"Sedjem/Sedjem": b"x", "Sedjem/../../evil.txt": b"evil"},
                        {"Sedjem/Sedjem": b"x", "../evil.txt": b"evil"},
                        {"Sedjem/Sedjem": b"x", "Sedjem/link": ("link", "/etc")},
                        {"Sedjem/Sedjem": b"x", "Sedjem/up": ("link", "../../..")}):
            with self.subTest(members=list(members)):
                self.gh.releases.clear()
                self.gh.release("0.3.0", files={"Sedjem-0.3.0-linux-x64.tar.gz": tar_gz(members)})
                up = self.build("linux")
                up.check()
                up.start_install()
                install = self.wait_install(up)
                self.assertEqual(install["state"], "failed", install)
                self.assertFalse((self.tmp / "evil.txt").exists() or (self.tmp / "apps" / "evil.txt").exists())
                self.assertEqual(sorted(p.name for p in (self.tmp / "apps").iterdir()), ["Sedjem"], "nothing left")

    def test_linux_wont_replace_a_folder_that_is_in_the_way(self):
        self.windows_release("0.3.0")
        (self.tmp / "apps" / "Sedjem-0.3.0").mkdir(parents=True)
        up = self.build("linux")
        up.check()
        up.start_install()
        install = self.wait_install(up)
        self.assertEqual(install["state"], "failed")
        self.assertIn("already there", install["error"])

    def test_only_this_repositorys_release_files_are_downloaded(self):
        up = self.build("windows")
        api = self.gh.url
        good_api = {"url": f"{api}/repos/{REPO}/releases/assets/123"}
        self.assertEqual(up.asset_url(good_api), (good_api["url"], {"Accept": "application/octet-stream"}))
        page = f"https://github.com/{REPO}/releases/download/v0.3.0/Sedjem-0.3.0-setup.exe"
        self.assertEqual(up.asset_url({"download": page}), (page, {}))
        for bad in ({"url": f"{api}/repos/someone/else/releases/assets/123"},
                    {"url": f"{api}/repos/{REPO}/releases/assets/123?x=1"},
                    {"url": f"{api}/repos/{REPO}/releases/assets/12a"},
                    {"url": f"{api}/repos/{REPO}/releases/assets/../../../x"},
                    {"url": f"http://127.0.0.2:{self.gh.server.server_address[1]}/repos/{REPO}/releases/assets/1"},
                    {"url": f"https://api.github.com/repos/{REPO}/releases/assets/123"},  # not the API Sedjem uses
                    {"download": page.replace("https://", "http://")},
                    {"download": page.replace("github.com", "github.com.evil.example")},
                    {"download": page.replace("github.com", "evil.example")},
                    {"download": page.replace(REPO, "someone/sedjem")},
                    {"download": page + "?download=1"},
                    {"download": page + "#x"},
                    {"download": f"https://github.com/{REPO}/releases/download/v0.3.0/../../../x/y"},
                    {"download": f"https://github.com/{REPO}/releases/download/v0.3.0/%2e%2e"},
                    {"download": f"https://github.com/{REPO}/archive/refs/tags/v0.3.0.zip"},
                    {"download": f"https://user@github.com/{REPO}/releases/download/v0.3.0/x.exe"},
                    {}):
            self.assertIsNone(up.asset_url(bad), bad)

    def test_checksum_files(self):
        sums = updates.parse_sums("\n".join([
            f"{'A' * 64}  Sedjem-0.3.0-setup.exe", f"{'b' * 64} *Sedjem-0.3.0-linux-x64.tar.gz",
            f"{'c' * 64}  ./SHA256SUMS-extra.txt", "not a line", f"{'d' * 63}  short.txt"]))
        self.assertEqual(sums, {"Sedjem-0.3.0-setup.exe": "a" * 64, "Sedjem-0.3.0-linux-x64.tar.gz": "b" * 64,
                                "SHA256SUMS-extra.txt": "c" * 64})


# ---------------------------------------------------------------------------------------------
# The restart
# ---------------------------------------------------------------------------------------------

class RestartTests(UpdatesCase):
    def test_the_same_command_line_again(self):
        for argv in (["/usr/bin/python3", "-P", "-m", "app", "--no-browser", "--port", "8799"],
                     [".venv/bin/python", "-m", "app.desktop", "--browser"],
                     ["pythonw.exe", "-m", "app", "--desktop"]):
            with mock.patch.object(sys, "orig_argv", argv):
                self.assertEqual(updates.restart_command(), [sys.executable, *argv[1:]])

    def test_restart_starts_it_and_quits(self):
        up = self.build("linux")
        with mock.patch.object(sys, "orig_argv", ["python", "-m", "app.desktop"]):
            up.restart()
        self.spawn.assert_called_once()
        call = self.spawn.call_args
        self.assertEqual(call.args[0], [sys.executable, "-m", "app.desktop"])
        self.assertEqual(call.kwargs["env"][updates.RESTART_ENV], f"{os.getpid()}:8799")
        self.assertEqual(call.kwargs["cwd"], os.getcwd())
        self.assertTrue((self.cfg.storage / "sedjem.log").exists(), "what the new process prints goes there")
        self.assertEqual(self.quits, [True])

    def test_if_the_new_version_stops_at_once_this_one_keeps_running(self):
        up = self.build("linux")
        self.spawn.return_value.poll.return_value = 1
        self.spawn.return_value.returncode = 1
        with self.assertRaises(updates.InstallError) as e:
            up.restart()
        self.assertIn("sedjem.log", str(e.exception))
        self.assertEqual(self.quits, [])

    def test_the_new_process_takes_the_port_back(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        threading.Timer(0.5, held.close).start()
        with mock.patch.dict(os.environ, {updates.RESTART_ENV: f"12345:{port}"}):
            t0 = time.monotonic()
            self.assertEqual(updates.restarted(wait=10), port)
            self.assertGreater(time.monotonic() - t0, 0.3, "it waited for the old process to let the port go")
            self.assertNotIn(updates.RESTART_ENV, os.environ, "processes it starts don't inherit the mark")
        self.assertIsNone(updates.restarted(), "an ordinary start")
        for bad in ("", "x:1", "1:0", "1:70000", "1"):
            with mock.patch.dict(os.environ, {updates.RESTART_ENV: bad}):
                self.assertIsNone(updates.restarted(wait=0.1), bad)

    def test_the_app_quits_the_way_settings_quit_does(self):
        server, app = make_server(self.cfg, port=0)
        self.addCleanup(server.server_close)
        closed = threading.Event()
        app.on_quit = closed.set
        app.quit(restarting=True)
        self.assertTrue(closed.wait(5))
        self.assertTrue(app.restarting, "the desktop app keeps the port file for the new process")


# ---------------------------------------------------------------------------------------------
# The HTTP API
# ---------------------------------------------------------------------------------------------

class ApiTests(UpdatesCase):
    def setUp(self):
        super().setUp()
        self.server, self.app = make_server(self.cfg, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.app.updates = self.build("linux")

    def call(self, method, path, body=None, headers=None):
        h = {"X-Sedjem": "1", **(headers or {})}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_the_state_and_its_shape(self):
        status, st = self.call("GET", "/api/updates")
        self.assertEqual(status, 200)
        self.assertEqual(set(st), {"enabled", "channel", "running", "checked", "checking", "error", "found", "note",
                                   "available", "install"})
        self.assertEqual(set(st["running"]), {"version", "commit", "date", "kind", "build_channel"})
        self.assertEqual((st["enabled"], st["channel"], st["available"], st["install"]), (None, "stable", None, None))

    def test_the_status_names_the_version_the_build_was_stamped_with(self):
        self.app.updates = self.build("linux", version="0.2.1-preview.20261021", commit=C)
        self.assertEqual(self.call("GET", "/api/status")[1]["version"], "0.2.1-preview.20261021")
        self.assertEqual(self.call("GET", "/api/updates")[1]["running"]["build_channel"], "preview")

    def test_first_start_question_check_pill_and_skip(self):
        self.assertEqual(self.call("GET", "/api/status")[1]["update"], {"ask": True, "available": False, "label": None})
        status, st = self.call("POST", "/api/updates/settings", {"enabled": False})
        self.assertEqual((status, st["enabled"]), (200, False))
        self.assertFalse(self.call("GET", "/api/status")[1]["update"]["ask"])
        self.windows_release("0.3.0")
        status, st = self.call("POST", "/api/updates/check")
        self.assertEqual((status, st["error"], st["found"], st["available"]["version"]), (200, None, "update", "0.3.0"))
        self.assertEqual(self.call("GET", "/api/status")[1]["update"],
                         {"ask": False, "available": True, "label": "Update available · 0.3.0"})
        status, st = self.call("POST", "/api/updates/skip", {"skip": True})
        self.assertEqual((status, st["available"]["skipped"]), (200, True))
        self.assertEqual(self.call("GET", "/api/status")[1]["update"], {"ask": False, "available": False, "label": None})
        self.assertTrue(self.call("POST", "/api/updates/skip", {"skip": False})[1]["available"]["skipped"] is False)
        self.assertEqual(self.call("POST", "/api/updates/skip", {"skip": "yes"})[0], 400)

    def test_settings_are_checked(self):
        self.assertEqual(self.call("POST", "/api/updates/settings", {"enabled": "yes"})[0], 400)
        self.assertEqual(self.call("POST", "/api/updates/settings", {"channel": "nightly"})[0], 400)
        status, st = self.call("POST", "/api/updates/settings", {"enabled": True, "channel": "latest"})
        self.assertEqual((status, st["enabled"], st["channel"]), (200, True, "latest"))
        self.assertEqual((self.cfg.setting("update_check"), self.cfg.setting("update_channel")), (True, "latest"))
        self.assertEqual(self.call("POST", "/api/settings", {"update_channel": "nightly"})[0], 400)
        self.assertEqual(self.call("GET", "/api/status")[1]["settings"]["update_channel"], "latest")

    def test_changes_need_the_header(self):
        for path, body in (("/api/updates/check", {}), ("/api/updates/settings", {"enabled": True}),
                           ("/api/updates/skip", {"skip": True}), ("/api/updates/install", {})):
            self.assertEqual(self.call("POST", path, body, headers={"X-Sedjem": ""})[0], 403, path)
        self.assertEqual(self.gh.calls, [])
        self.assertIsNone(self.cfg.setting("update_check"))

    def test_update_now(self):
        status, body = self.call("POST", "/api/updates/install")
        self.assertEqual(status, 409)
        self.assertIn("no update", body["error"])
        self.windows_release("0.3.0")
        self.call("POST", "/api/updates/check")
        status, st = self.call("POST", "/api/updates/install")
        self.assertEqual(status, 200)
        self.assertIsNotNone(st["install"])
        end = time.time() + 30
        while st["install"]["state"] not in ("done", "failed") and time.time() < end:
            time.sleep(0.05)
            st = self.call("GET", "/api/updates")[1]
        self.assertEqual((st["install"]["state"], st["install"]["folder"]), ("done", str(self.tmp / "apps" / "Sedjem-0.3.0")))

    def test_update_now_waits_for_running_transcriptions_when_it_restarts(self):
        self.app.updates = self.build("windows")
        self.windows_release("0.3.0")
        self.call("POST", "/api/updates/check")
        with mock.patch.object(self.app.store, "list", return_value=[{"id": "x", "status": "running"}]):
            status, body = self.call("POST", "/api/updates/install")
        self.assertEqual(status, 409)
        self.assertIn("transcriptions", body["error"])
        self.spawn.assert_not_called()

    def test_check_now_when_offline(self):
        self.gh.mode = "offline"
        status, st = self.call("POST", "/api/updates/check")
        self.assertEqual((status, st["error"], st["checking"]), (200, "offline", False))


if __name__ == "__main__":
    unittest.main()
