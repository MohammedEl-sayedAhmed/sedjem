"""Browser tests of the secret between the app's server and its own window (app/session.py): the page that the app
writes for a browser tab (<storage>/open-sedjem.html) posts the secret to /api/login, and the page that answers keeps
it in the session storage of the app's own address and goes on to the app. So the app loads, no cookie is set, and the API
answers every call of the page. A browser that opens the address of the server directly has no secret, is shown that
it has no access, and the API refuses its calls. The page's preferences come from the data folder, since the page's
own storage is new with each port.
Headless Chromium against a Sedjem server (see tests/ui_support.py). Every page here opens in a context of its own,
without the secret that UiTestCase puts in the storage of the others.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_session.py" -v
"""
import json
import time
import unittest

from ui_support import UiTestCase


class Session(UiTestCase):
    def fresh_page(self):
        """A page in a new browser context that has nothing stored, as a tab that nobody has logged in."""
        context = self.browser.new_context()
        self.addCleanup(context.close)
        return context, context.new_page()

    def test_the_login_page_stores_the_secret_and_opens_the_app(self):
        port = self.server.server_address[1]
        login = self.app.session.login_page(port)
        context, page = self.fresh_page()
        errors, statuses = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("response", lambda r: statuses.append(r.status) if "/api/" in r.url else None)
        page.goto(login.as_uri())
        page.wait_for_url(f"http://127.0.0.1:{port}/", timeout=15_000)
        page.wait_for_function("() => typeof S !== 'undefined' && S.status", timeout=30_000)
        self.assertEqual(context.cookies(self.base), [], "the secret is never in a cookie")
        self.assertEqual(page.evaluate("sessionStorage.getItem('sedjem-token')"), self.app.session.secret)
        self.assertIsNone(page.evaluate("localStorage.getItem('sedjem-token')"), "it isn't kept after the tab closes")
        self.assertEqual(page.locator("#noAccess").count(), 0)
        self.assertTrue(statuses)
        self.assertEqual(set(statuses), {200}, "every call of the page to the API is answered")
        self.assertEqual(errors, [])

    def test_a_browser_without_the_secret_has_no_access(self):
        _, page = self.fresh_page()
        statuses = []
        page.on("response", lambda r: statuses.append(r.status) if "/api/status" in r.url else None)
        page.goto(f"{self.base}/")
        page.wait_for_selector("#noAccess", timeout=15_000)
        self.assertNotIn(200, statuses)
        self.assertEqual(page.evaluate("fetch('/api/status').then(r => r.status)"), 401)


    def test_the_preferences_in_the_data_folder_are_shown_and_kept(self):
        prefs = self.app.cfg.storage / "page.json"
        kept = {"sedjem-theme": "dark", "sedjem-sort": "oldest", "sedjem-video-large": "1"}
        prefs.write_text(json.dumps(kept), encoding="utf-8")
        self.addCleanup(prefs.unlink, missing_ok=True)
        self.open()
        self.assertEqual(self.page.evaluate("document.documentElement.dataset.theme"), "dark")
        self.assertEqual(self.page.input_value("#jobSort"), "oldest")
        self.assertEqual(self.page.get_attribute("#videoSize", "aria-pressed"), "true")
        self.page.wait_for_timeout(500)
        self.assertEqual(json.loads(prefs.read_text(encoding="utf-8")), kept, "the page's start saved nothing over them")
        self.page.evaluate("document.getElementById('themeBtn').click()")  # (over any dialog the first start shows)
        deadline = time.monotonic() + 10
        while json.loads(prefs.read_text(encoding="utf-8")).get("sedjem-theme") != "light" and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(json.loads(prefs.read_text(encoding="utf-8"))["sedjem-theme"], "light")


if __name__ == "__main__":
    unittest.main()
