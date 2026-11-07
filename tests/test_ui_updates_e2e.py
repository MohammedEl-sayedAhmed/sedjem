"""End to end: the real update engine (app/updates.py) and the real Settings → Updates (app/static/updates.js),
together, against the stand-in for GitHub (tests/github_support.py): the first-start question, Check now, the pill,
the notice once, the release notes (escaped), skipping, and the Latest channel. The stand-in works out commits from a
throwaway clone of this checkout with two new commits on main: the first is the release, the second the newest one
whose tests passed. Update now is never pressed: from source it would run git on this checkout.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import json
import shutil
import tempfile
import unittest
import urllib.request
from pathlib import Path

from ui_support import UiTestCase  # (puts the project on the path)
from github_support import FakeGitHub
from test_updates import git, rmtree
from app import updates

ROOT = Path(__file__).resolve().parent.parent


class UpdatesEndToEnd(UiTestCase):
    @classmethod
    def setUpClass(cls):
        run = updates.running()
        if run["kind"] != "source" or not run["commit"] or not shutil.which("git"):
            raise unittest.SkipTest("needs git and a git checkout of Sedjem")
        major, minor = (int(n) for n in run["version"].split(".")[:2])
        cls.next = f"{major}.{minor + 1}.0"  # the release above the running version
        cls.gh = FakeGitHub()
        cls.addClassCleanup(cls.gh.close)
        cls.clone = Path(tempfile.mkdtemp(prefix="sedjem-e2e-clone-"))
        cls.addClassCleanup(rmtree, cls.clone)
        git(cls.clone, "init", "-q")
        git(cls.clone, "fetch", "-q", "--depth", "1", str(ROOT), run["commit"])
        git(cls.clone, "checkout", "-q", "-b", "main", run["commit"])
        for title in ("Updates: a first change on main", "Updates: a second change on main"):
            git(cls.clone, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty",
                "-m", title)
        git(cls.clone, "tag", f"v{cls.next}", "HEAD~1")
        cls.gh.git = cls.clone / ".git"
        cls.gh.release(cls.next, notes=f"Sedjem {cls.next} **tells you** when a newer version is out.\n\n"
                                       "- Update now in Settings\n- <script>window.__pwned = 1</script>\n",
                       files={f"Sedjem-{cls.next}-setup.exe": b"MZ setup",
                              f"Sedjem-{cls.next}-linux-x64.tar.gz": b"archive"})
        cls.gh.run(git(cls.clone, "rev-parse", "HEAD"))  # the second change's Tests passed
        super().setUpClass()
        cls.app.updates.api = cls.gh.url  # the stand-in, not api.github.com

    def settings(self, **values):
        """settings.json as a first start would have it, plus values."""
        path = self.home / "app_data" / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        for key in ("update_check", "update_skip", "update_channel"):
            data.pop(key, None)
        path.write_text(json.dumps({**data, **values}), encoding="utf-8")

    def call(self, method, path):
        """The API straight from the test (no page needed), with the header every change needs."""
        req = urllib.request.Request(self.base + path, method=method, data=b"{}" if method == "POST" else None,
                                     headers={"X-Sedjem": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def pill(self):
        return self.page.locator(".topbar-status [data-open-settings='updates']")

    def test_stable_from_the_question_to_skipping(self):
        self.settings()  # not asked yet
        self.open()
        self.page.wait_for_selector("text=Check for updates automatically?")
        with self.page.expect_response(lambda r: r.url.endswith("/api/updates/settings")):
            self.page.click("text=Yes, check daily")
        self.assertIs(self.call("GET", "/api/updates")["enabled"], True)

        self.open_settings("updates")
        self.page.click("#settings button:has-text('Check now')")
        self.page.wait_for_selector("#settings strong:has-text('tells you')")
        self.assertIn("/repos/MohammedEl-sayedAhmed/sedjem/releases", self.gh.paths())
        text = self.page.inner_text("#settings")
        self.assertIn(f"Sedjem {self.next}", text)
        self.assertIn("<script>window.__pwned = 1</script>", text, "a tag in the notes stays text")
        self.assertIsNone(self.page.evaluate("() => window.__pwned"))
        available = self.call("GET", "/api/updates")["available"]
        self.assertEqual((available["version"], available["ahead"]), (self.next, 1))
        if available["blocked"]:  # this checkout isn't a clean main (a branch, a pull request): why, and by hand
            self.assertIn(available["blocked"], text)
            self.assertEqual(self.page.locator("#updInstall").count(), 0)
        else:  # offered, not pressed
            self.assertTrue(self.page.locator("#updInstall").is_visible())

        self.page.click("#settings button:has-text('Close')")
        self.page.wait_for_function("(label) => document.querySelector('.topbar-status')?.innerText.includes(label)",
                                    arg=f"Update available · {self.next}")
        self.assertEqual(self.pill().count(), 1)

        self.open_settings("updates")
        self.page.click("#settings button:has-text('Skip this version')")
        self.page.wait_for_selector("#settings button:has-text(\"Don't skip\")")
        self.assertIs(self.call("GET", "/api/updates")["available"]["skipped"], True)
        self.page.click("#settings button:has-text('Close')")
        self.page.wait_for_function("() => !document.querySelector('.topbar-status [data-open-settings=\"updates\"]')")
        self.open_settings("updates")
        self.page.click("#settings button:has-text(\"Don't skip\")")
        self.page.wait_for_selector("#settings button:has-text('Skip this version')")

    def test_the_notice_once_then_not_again(self):
        self.settings(update_check=True)
        self.call("POST", "/api/updates/check")
        self.open()
        self.page.wait_for_selector(f"text=Sedjem {self.next} is out")
        self.page.click("text=Later")
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.page.wait_for_timeout(800)
        self.assertEqual(self.page.locator(f"text=Sedjem {self.next} is out").count(), 0, "shown once")
        self.assertEqual(self.pill().count(), 1, "the pill stays")

    def test_latest_lists_the_tested_changes_on_main(self):
        self.settings(update_check=True, update_channel="latest")
        state = self.call("POST", "/api/updates/check")
        self.assertEqual((state["available"] or {}).get("ahead"), 2, state)
        self.open()
        self.page.wait_for_function("() => document.querySelector('.topbar-status')?.innerText.includes('2 new changes')")
        self.open_settings("updates")
        self.page.wait_for_selector("#settings :text('Updates: a second change on main')")
        self.assertIn("Updates: a first change on main", self.page.inner_text("#settings"))


if __name__ == "__main__":
    unittest.main()
