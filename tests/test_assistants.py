"""Tests for what assistants may see over MCP (app/assistants.py): the two switches and hiding one by one, the
settings' checks, the access log (kept short, never failing a request), and the connection snippets.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import assistants  # noqa: E402
from app.config import Config  # noqa: E402

MEETING = {"id": "20261019-100000-aaaa", "title": "Sprint planning"}
LECTURE = {"id": "20261019-110000-bbbb", "title": "Intro", "youtube_id": "vidpython01",
           "course": {"id": "PLcourse", "title": "Python", "index": 1, "count": 3}}
VIDEO = {"id": "20261019-120000-cccc", "title": "A talk", "youtube_id": "vidalone001"}


class Visibility(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-assistants-"))
        self.cfg = Config(home=self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def seen(self):
        return [j["title"] for j in (MEETING, LECTURE, VIDEO) if assistants.visible(self.cfg, j)]

    def test_everything_is_visible_by_default(self):
        self.assertEqual(self.seen(), ["Sprint planning", "Intro", "A talk"])

    def test_the_two_switches(self):
        self.cfg.save_settings(mcp_recordings=False)
        self.assertEqual(self.seen(), ["Intro", "A talk"], "a YouTube video on its own counts with the courses")
        self.cfg.save_settings(mcp_recordings=True, mcp_courses=False)
        self.assertEqual(self.seen(), ["Sprint planning"])

    def test_hiding_a_transcription_or_a_course(self):
        assistants.set_hidden(self.cfg, f"job:{MEETING['id']}", True)
        self.assertEqual(self.seen(), ["Intro", "A talk"])
        assistants.set_hidden(self.cfg, "course:PLcourse", True)
        self.assertEqual(self.seen(), ["A talk"], "every lecture of the course, new ones too")
        assistants.set_hidden(self.cfg, f"job:{MEETING['id']}", False)
        assistants.set_hidden(self.cfg, f"job:{MEETING['id']}", False)  # showing twice is harmless
        self.assertEqual(self.seen(), ["Sprint planning", "A talk"])
        self.assertEqual(self.cfg.setting("mcp_hidden"), ["course:PLcourse"])

    def test_a_bad_entry_saved_by_hand_does_not_block_changes(self):
        self.cfg.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.settings_path.write_text(json.dumps({"mcp_hidden": ["job:../x", 7, "course:PLcourse"]}), encoding="utf-8")
        assistants.set_hidden(self.cfg, f"job:{MEETING['id']}", True)
        self.assertEqual(self.cfg.setting("mcp_hidden"), ["course:PLcourse", f"job:{MEETING['id']}"])
        self.cfg.settings_path.write_text(json.dumps({"mcp_hidden": "not a list"}), encoding="utf-8")
        assistants.set_hidden(self.cfg, "course:PLcourse", True)
        self.assertEqual(self.cfg.setting("mcp_hidden"), ["course:PLcourse"])

    def test_a_broken_hidden_setting_hides_nothing_instead_of_failing(self):
        for broken in (7, "job:x", {"job": 1}, [None, 3]):
            self.cfg.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.settings_path.write_text(json.dumps({"mcp_hidden": broken}), encoding="utf-8")
            self.assertEqual(self.seen(), ["Sprint planning", "Intro", "A talk"], broken)

    def test_one_settings_read_for_many_jobs(self):
        s = self.cfg.settings()
        with mock.patch.object(self.cfg, "settings", side_effect=AssertionError("read again")):
            self.assertTrue(assistants.visible(self.cfg, MEETING, s))

    def test_bad_values_are_refused(self):
        for key in ("job:../../etc", "course:", "job:20261019-100000-AAAA", "meeting:x", "", None, "course:a b"):
            with self.assertRaises(ValueError, msg=key):
                assistants.set_hidden(self.cfg, key, True)
        for value in ("job:20261019-100000-aaaa", ["job:x"], [1], {"job": 1}):
            with self.assertRaises(ValueError):
                self.cfg.save_settings(mcp_hidden=value)
        for key in ("mcp_courses", "mcp_recordings"):
            with self.assertRaises(ValueError):
                self.cfg.save_settings(**{key: "yes"})
        self.assertEqual(self.cfg.settings()["mcp_hidden"], [])

    def test_an_entry_changed_by_hand_does_not_block_hiding(self):
        for bad in (["job:../../etc", 5, f"job:{MEETING['id']}"], "job:x", 7):
            saved = json.loads(self.cfg.settings_path.read_text(encoding="utf-8")) if self.cfg.settings_path.exists() else {}
            self.cfg.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.settings_path.write_text(json.dumps({**saved, "mcp_hidden": bad}), encoding="utf-8")
            assistants.set_hidden(self.cfg, "course:PLcourse", True)
            kept = [f"job:{MEETING['id']}"] if isinstance(bad, list) else []
            self.assertEqual(self.cfg.setting("mcp_hidden"), kept + ["course:PLcourse"], bad)


class Log(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-assistants-"))
        self.cfg = Config(home=self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_newest_first(self):
        assistants.log(self.cfg, "Claude Desktop 1.2", "search", "tokenizers", ["20261019-110000-bbbb"])
        assistants.log(self.cfg, "Cursor", "get_transcript", "Intro 00:00-10:00")
        got = assistants.read_log(self.cfg)
        self.assertEqual([(e["client"], e["action"]) for e in got], [("Cursor", "get_transcript"), ("Claude Desktop 1.2", "search")])
        self.assertEqual(got[1]["ids"], ["20261019-110000-bbbb"])
        self.assertEqual(assistants.read_log(self.cfg, limit=1)[0]["action"], "get_transcript")

    def test_kept_short_and_never_failing(self):
        with mock.patch.object(assistants, "LOG_KEEP", 5), mock.patch.object(assistants, "LOG_TRIM_AT", 8):
            for i in range(20):
                assistants.log(self.cfg, "c", "search", f"q{i}")
        lines = assistants.log_path(self.cfg).read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 8)
        self.assertEqual(json.loads(lines[-1])["detail"], "q19")
        with open(assistants.log_path(self.cfg), "a", encoding="utf-8") as f:
            f.write('{"half an entry\n')
        self.assertEqual(assistants.read_log(self.cfg)[0]["detail"], "q19", "a broken line is skipped")
        with mock.patch("builtins.open", side_effect=PermissionError("read-only disk")):
            assistants.log(self.cfg, "c", "search")  # no exception
        assistants.clear_log(self.cfg)
        self.assertEqual(assistants.read_log(self.cfg), [])


class Snippets(unittest.TestCase):
    def test_each_assistant_gets_a_working_configuration(self):
        cfg = Config(home=ROOT)
        got = {s["client"]: s for s in assistants.snippets(cfg)}
        self.assertEqual(list(got), ["Claude Desktop", "Claude Code", "Cursor", "VS Code"])
        command, args, env = assistants.launch(cfg)
        for client in ("Claude Desktop", "Cursor"):
            server = json.loads(got[client]["text"])["mcpServers"]["sedjem"]
            self.assertEqual((server["command"], server["args"]), (command, args))
        vscode = json.loads(got["VS Code"]["text"])["servers"]["sedjem"]
        self.assertEqual(vscode["type"], "stdio")
        line = got["Claude Code"]["text"]
        self.assertTrue(line.startswith("claude mcp add --scope user sedjem"))
        self.assertIn(" -- ", line)
        if not assistants.FROZEN:
            self.assertEqual(args, ["-P", "-m", "app.mcp"])
            self.assertEqual(env["PYTHONPATH"], str(ROOT))

    def test_from_source_on_windows_the_console_python(self):
        folder = Path(tempfile.mkdtemp(prefix="sedjem-assistants-"))
        try:
            for name in ("python.exe", "pythonw.exe"):
                (folder / name).write_bytes(b"")
            # app.cmd starts Sedjem with pythonw.exe; the MCP server needs the streams of python.exe
            self.assertEqual(assistants.console_python(str(folder / "pythonw.exe")), str(folder / "python.exe"))
            self.assertEqual(assistants.console_python(str(folder / "python.exe")), str(folder / "python.exe"))
            (folder / "python.exe").unlink()
            self.assertEqual(assistants.console_python(str(folder / "pythonw.exe")), str(folder / "pythonw.exe"),
                             "kept when there is no python.exe beside it")
            self.assertEqual(assistants.console_python("/usr/bin/python3"), "/usr/bin/python3")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_another_data_folder_is_passed_on(self):
        home = Path(tempfile.mkdtemp(prefix="sedjem-assistants-"))
        try:
            _, _, env = assistants.launch(Config(home=home))
            self.assertEqual(env["SEDJEM_HOME"], str(home))
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
