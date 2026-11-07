"""Tests for what a failed browser test leaves for CI (tests/ui_support.py, docs/14-pipeline.md): with
SEDJEM_UI_TRACES set to a folder, a test that fails, by an assertion or by an error its page logged, leaves there its
Playwright trace and a screenshot of its page, and a test that passes leaves nothing. Runs a few browser tests of its
own, in their own runner, against the demo server.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import ui_support
from ui_support import UiTestCase


class FailedBrowserTests(unittest.TestCase):
    def test_a_failed_test_leaves_its_trace_and_a_screenshot(self):
        # defined here, so that discovery doesn't run these tests as the suite's own
        class Page(UiTestCase):
            def test_passes(self):
                self.open()

            def test_fails(self):
                self.open()
                self.assertEqual(self.page.title(), "not the title")

            def test_logs_an_error(self):
                self.open()
                self.page.evaluate("console.error('broken on purpose')")

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(ui_support, "TRACES", folder):
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(Page)
            result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
            if result.skipped:
                self.skipTest(result.skipped[0][1])
            self.assertEqual(result.errors, [], "the browser started")
            failed = sorted(test.id().rsplit(".", 1)[1] for test, _ in result.failures)
            self.assertEqual(failed, ["test_fails", "test_logs_an_error"])
            files = sorted(Path(folder).iterdir())
            self.assertEqual(sorted(p.name.split("Page.")[1] for p in files),
                             ["test_fails.png", "test_fails.zip", "test_logs_an_error.png", "test_logs_an_error.zip"])
            for path in files:
                if path.suffix == ".png":
                    self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                else:
                    with zipfile.ZipFile(path) as trace:
                        self.assertTrue(any(n.endswith(".trace") for n in trace.namelist()), trace.namelist())


if __name__ == "__main__":
    unittest.main()
