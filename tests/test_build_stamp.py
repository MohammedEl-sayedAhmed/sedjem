"""Tests for the build stamp (desktop/build.py): what a desktop build says about itself in app/_build.json,
which the app reads for updates. The version and channel come from SEDJEM_VERSION and SEDJEM_CHANNEL or from
app/__init__.py, the commit and its date from git; a wrong version or a channel that contradicts the version
stops the build. The stamp is bundled, removed from the source tree afterwards, and never committed.
PyInstaller is not run: its entry point is replaced.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sedjem_build", ROOT / "desktop" / "build.py")
assert spec and spec.loader
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
sys.path.insert(0, str(ROOT))
import app  # noqa: E402

NOW = datetime(2026, 10, 21, 2, 41, 7, tzinfo=timezone.utc)
SHA = "6eb9596ce96112f41903618d0e5197d7e087a3a6"


def fake_git(*args):
    return {("rev-parse", "HEAD"): SHA, ("show", "-s", "--format=%ct", "HEAD"): "1792440160"}.get(args)


class Stamp(unittest.TestCase):
    def test_by_default_the_app_version_the_commit_and_stable(self):
        with mock.patch.object(build, "git", fake_git):
            info = build.stamp({}, NOW)
        self.assertEqual(info, {"version": app.__version__, "commit": SHA, "date": "2026-10-19T20:02:40Z",
                                "channel": "stable", "built": "2026-10-21T02:41:07Z"})

    def test_the_workflow_names_a_preview(self):
        env = {"SEDJEM_VERSION": "0.2.1-preview.20261021", "SEDJEM_CHANNEL": "preview"}
        with mock.patch.object(build, "git", fake_git):
            info = build.stamp(env, NOW)
        self.assertEqual((info["version"], info["channel"], info["commit"]), ("0.2.1-preview.20261021", "preview", SHA))
        with mock.patch.object(build, "git", fake_git):
            self.assertEqual(build.stamp({"SEDJEM_VERSION": "0.2.1-preview.20261021.2"}, NOW)["channel"], "preview",
                             "without SEDJEM_CHANNEL, the version decides")
            self.assertEqual(build.stamp({"SEDJEM_VERSION": "0.3.0", "SEDJEM_CHANNEL": "stable"}, NOW)["channel"], "stable")

    def test_a_wrong_version_or_channel_stops_the_build(self):
        for env in ({"SEDJEM_VERSION": "0.3.0-beta"}, {"SEDJEM_VERSION": "v0.3.0"},
                    {"SEDJEM_VERSION": "0.3.0-preview.2026102"}, {"SEDJEM_VERSION": "0.3.0-preview.20261021.0"},
                    {"SEDJEM_VERSION": "0.3.0", "SEDJEM_CHANNEL": "preview"},
                    {"SEDJEM_VERSION": "0.3.1-preview.20261021", "SEDJEM_CHANNEL": "stable"},
                    {"SEDJEM_CHANNEL": "nightly"}):
            with self.subTest(env=env), mock.patch.object(build, "git", fake_git):
                with self.assertRaises(ValueError):
                    build.stamp(env, NOW)

    def test_without_git_the_commit_is_unknown(self):
        with mock.patch.object(build, "git", lambda *args: None):
            info = build.stamp({}, NOW)
        self.assertIsNone(info["commit"])
        self.assertIsNone(info["date"])

    def test_the_real_checkout(self):
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        except OSError:
            self.skipTest("no git")
        if head.returncode != 0:
            self.skipTest("not a git checkout")
        info = build.stamp({})
        self.assertEqual(info["commit"], head.stdout.strip())
        self.assertRegex(info["commit"], r"^[0-9a-f]{40}$")
        for key in ("date", "built"):
            self.assertRegex(info[key], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class Bundle(unittest.TestCase):
    def test_the_stamp_is_bundled_and_then_removed(self):
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stamp_file = root / "app" / "_build.json"
            stamp_file.parent.mkdir()

            def run(opts):  # PyInstaller: copies the stamp where a build has it
                seen["opts"] = opts
                seen["stamp"] = json.loads(stamp_file.read_text(encoding="utf-8"))
                bundled = root / "dist" / "Sedjem" / "_internal" / "app"
                bundled.mkdir(parents=True)
                (bundled / "_build.json").write_text(stamp_file.read_text(encoding="utf-8"), encoding="utf-8")

            pyinstaller = types.ModuleType("PyInstaller")
            pyinstaller.__main__ = types.SimpleNamespace(run=run)
            with mock.patch.object(build, "ROOT", root), mock.patch.object(build, "STAMP", stamp_file), \
                    mock.patch.object(build, "git", fake_git), mock.patch.object(sys, "argv", ["build.py"]), \
                    mock.patch.dict(sys.modules, {"PyInstaller": pyinstaller, "PyInstaller.__main__": pyinstaller.__main__}), \
                    mock.patch.dict(build.os.environ, {"SEDJEM_VERSION": "0.2.1-preview.20261021", "SEDJEM_CHANNEL": "preview"}), \
                    mock.patch("builtins.print"):
                build.main()
            self.assertFalse(stamp_file.exists(), "a source checkout never keeps a stamp")
        opts = seen["opts"]
        self.assertIn(f"{stamp_file}{build.os.pathsep}app", [opts[i + 1] for i, o in enumerate(opts) if o == "--add-data"])
        self.assertEqual(seen["stamp"]["version"], "0.2.1-preview.20261021")
        self.assertEqual(set(seen["stamp"]), {"version", "commit", "date", "channel", "built"})

    def test_a_build_without_its_stamp_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "app").mkdir()
            pyinstaller = types.ModuleType("PyInstaller")
            pyinstaller.__main__ = types.SimpleNamespace(run=lambda opts: None)  # builds nothing
            with mock.patch.object(build, "ROOT", root), mock.patch.object(build, "STAMP", root / "app" / "_build.json"), \
                    mock.patch.object(build, "git", fake_git), mock.patch.object(sys, "argv", ["build.py"]), \
                    mock.patch.dict(sys.modules, {"PyInstaller": pyinstaller, "PyInstaller.__main__": pyinstaller.__main__}), \
                    mock.patch.dict(build.os.environ, {"SEDJEM_VERSION": "0.3.0", "SEDJEM_CHANNEL": ""}):
                with self.assertRaises(SystemExit) as stop:
                    build.main()
            self.assertIn("no stamp", str(stop.exception.code))
            self.assertFalse((root / "app" / "_build.json").exists())

    def test_git_never_sees_a_stamp(self):
        self.assertEqual(build.STAMP, ROOT / "app" / "_build.json")
        self.assertIn("app/_build.json", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        try:
            ignored = subprocess.run(["git", "check-ignore", "-q", "app/_build.json"], cwd=ROOT, timeout=30)
        except OSError:
            return
        if subprocess.run(["git", "rev-parse"], cwd=ROOT, capture_output=True, timeout=30).returncode == 0:
            self.assertEqual(ignored.returncode, 0, "git check-ignore app/_build.json")


if __name__ == "__main__":
    unittest.main()
