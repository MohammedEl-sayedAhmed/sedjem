"""Browser tests of updates in the app (app/static/updates.js): the first-start question and both answers, the
top-bar pill, the notice once for each new version (not again after a reload, not for a skipped one), Settings →
Updates on both channels with the release notes and the list of changes, Check now and its errors, Skip this
version and Don't skip, Update now where it can't run (the commands, copied), its progress through the restart
and the reload, a failure and Try again, the restart that never comes back, release notes drawn safely, the dark
theme and a narrow window.
The server's side of updates (app/updates.py) is stood in for: FakeUpdates answers /api/updates and its POSTs and
adds "update" to /api/status, in the shapes agreed for issue #11, through Playwright's request interception.
Headless Chromium against a Sedjem server with demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_updates.py" -v
"""
import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ui_support import UiTestCase

REPO = "https://github.com/MohammedEl-sayedAhmed/sedjem"
REFUSED = "Failed to load resource: net::ERR_CONNECTION_REFUSED"  # how the browser logs the server being away
CONFLICT = "Failed to load resource: the server responded with a status of 409"
NOTES = """Sedjem 0.3.0 tells you when a newer version is out, and updates itself.

## Download

- Windows 10 and 11: `Sedjem-0.3.0-setup.exe` installs for your user. It isn't signed yet, so SmartScreen
  warns: choose *More info*, then *Run anyway*.
- Linux (x64): unpack `Sedjem-0.3.0-linux-x64.tar.gz` and run `Sedjem/Sedjem`.
  - `SHA256SUMS.txt` has the checksums of both.

## Updates

1. Settings → Updates has the **Stable releases** and **Latest changes** channels.
2. From source, Update now runs:

```
git pull --ff-only
```

See [the docs](https://github.com/MohammedEl-sayedAhmed/sedjem/tree/main/docs) for what is sent to GitHub.

**Full Changelog**: https://github.com/MohammedEl-sayedAhmed/sedjem/compare/v0.2.0...v0.3.0
"""
HOSTILE = """<script>window.__pwned = 1</script>

<img src=x onerror="window.__pwned = 2"> and <b>bold</b> <i>italic</i>

[click](javascript:window.__pwned=3)

[a "quoted" link](https://example.com/"><script>window.__pwned=4</script>)

`<iframe src="https://example.com">`
"""


def ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha(text):
    return hashlib.sha1(text.encode()).hexdigest()


def running(**fields):
    """The version that runs: from a source checkout, unless fields say otherwise."""
    return {"version": "0.2.0", "commit": sha("v0.2.0"), "date": "2026-10-19T12:00:00Z", "kind": "source",
            "build_channel": None, **fields}


def release(**fields):
    """What the Stable channel offers: a newer release, with its notes."""
    return {"channel": "stable", "version": "0.3.0", "commit": sha("v0.3.0"), "date": "2026-10-21T12:00:00Z",
            "title": "Sedjem 0.3.0", "notes": NOTES, "changes": [], "ahead": 0, "url": f"{REPO}/releases/tag/v0.3.0",
            "skipped": False, "action": "git", "blocked": None, "commands": [], **fields}


TITLES = ["Updates: the notice once for each new version", "Settings: an Updates tab with the two channels",
          "Server: a longer listen queue for a page's burst of connections", "Assistants: a path that needs no change is kept",
          "Docs: what an update sends to GitHub"]


def changes(ahead=12, **fields):
    """What the Latest channel offers: the newest tested changes on main, newest first (some of them)."""
    items = [{"sha": sha(t), "title": t, "date": ago(hours=3 + 20 * i), "url": f"{REPO}/commit/{sha(t)}"}
             for i, t in enumerate(TITLES)]
    return {"channel": "latest", "version": None, "commit": items[0]["sha"], "date": items[0]["date"],
            "title": f"{ahead} new changes", "notes": None, "changes": items, "ahead": ahead,
            "url": f"{REPO}/compare/{sha('v0.2.0')}...{items[0]['sha']}", "skipped": False, "action": "git",
            "blocked": None, "commands": [], **fields}


def install(state, **fields):
    return {"state": state, "done": 0, "total": 48_000_000, "error": None, "folder": None, **fields}


class FakeUpdates:
    """The server's side of updates, as the page sees it: GET /api/updates and its POSTs answered from state, and
    "update" added to the real /api/status. calls lists the requests to /api/updates*; check is what Check now finds
    (a function that changes the state); offers is what each channel offers once it is chosen; a POST named in hold
    waits until release(); down makes that many status checks fail (the server away while it restarts); refuse
    answers a POST with an error."""

    def __init__(self, page, ask=False, **state):
        self.ask = ask
        self.state = {"enabled": True, "channel": "stable", "running": running(), "checked": ago(hours=2),
                      "checking": False, "error": None, "found": None, "note": None, "available": None, "install": None,
                      **state}
        self.calls, self.hold, self.held, self.offers, self.refuse = [], set(), [], {}, {}
        self.check, self.down = None, 0
        page.route(lambda url: urlparse(url).path.startswith("/api/updates"), self.updates)
        page.route(lambda url: urlparse(url).path == "/api/status", self.status)

    def label(self):
        a = self.state["available"]
        if not a or a["skipped"]:  # a skipped one isn't announced
            return None
        return f"Update available · {a['version']}" if a["channel"] == "stable" else f"{a['ahead']} new changes"

    def status(self, route):
        if self.down:
            self.down -= 1
            route.abort("connectionrefused")
            return
        response = route.fetch()
        status = response.json()
        status["update"] = {"ask": self.ask, "available": self.label() is not None, "label": self.label()}
        route.fulfill(response=response, json=status)

    def updates(self, route):
        request = route.request
        what = urlparse(request.url).path.removeprefix("/api/updates").strip("/") or "state"
        body = request.post_data_json if request.method == "POST" else None
        self.calls.append((request.method, what, body, request.headers.get("x-sedjem")))
        if what in self.hold:
            self.held.append((what, route, body))
        else:
            self.answer(what, route, body)

    def answer(self, what, route, body):
        s = self.state
        if what in self.refuse:
            route.fulfill(status=409, json={"error": self.refuse[what]})
            return
        if what == "check":
            if self.check:
                self.check(s)
            else:
                s.update(checked=ago(seconds=1), error=None)
        elif what == "settings":
            if "enabled" in body:
                s["enabled"], self.ask = body["enabled"], False
            if "channel" in body:
                s["channel"] = body["channel"]
                if body["channel"] in self.offers:
                    s["available"] = self.offers[body["channel"]]
        elif what == "skip":
            s["available"]["skipped"] = body["skip"]
        elif what == "install":
            s["install"] = install("downloading")
        route.fulfill(json=s)

    def release(self, what):
        """Answers the requests held for what."""
        for item in [h for h in self.held if h[0] == what]:
            self.held.remove(item)
            self.answer(*item)

    def posts(self, what):
        return [body for method, name, body, _ in self.calls if method == "POST" and name == what]


class Updates(UiTestCase):
    jobs = ({"jid": "20261020-100000-a001", "title": "Sprint planning", "created": "2026-10-20T10:00:00+03:00"},)

    def setUp(self):
        super().setUp()
        self.updates = None

    def tearDown(self):
        try:
            if self.updates:
                posts = [c for c in self.updates.calls if c[0] == "POST"]
                self.assertTrue(all(c[3] == "1" for c in posts), f"every change sends the X-Sedjem header: {posts}")
            self.page.unroute_all(behavior="ignoreErrors")
        finally:
            super().tearDown()

    def fake(self, **state):
        self.updates = FakeUpdates(self.page, **state)
        return self.updates

    def ready(self):
        """After a reload: the app has its status, and what it fetched about updates has arrived."""
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.page.wait_for_function("() => !UPD.loading")

    def open_tab(self):
        self.open_settings("updates")
        self.page.wait_for_selector("#settings #updAuto")

    def text(self, selector):
        return " ".join(self.page.inner_text(selector).split())

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())")

    def stored(self, key):
        return self.page.evaluate("(k) => localStorage.getItem(k)", key)

    # ---- the first-start question ----------------------------------------------------------------------------
    def test_the_first_start_question_yes(self):
        fake = self.fake(ask=True, enabled=None, checked=None)
        self.open()
        self.page.wait_for_selector("#updAsk[open]")
        self.assertEqual(self.page.inner_text("#updAskTitle"), "Check for updates automatically?")
        text = self.text("#updAskText")
        for words in ("Once a day, Sedjem can ask GitHub whether a newer version is out", "IP address", "nothing else is sent"):
            self.assertIn(words, text)
        self.assertEqual(self.texts("#updAsk .dlg-foot button"), ["No", "Yes, check daily"])
        self.page.click("#updAskYes")
        self.page.wait_for_selector("#updAsk", state="hidden")
        self.page.wait_for_selector(".toast >> text=Sedjem checks for updates once a day")
        self.assertEqual(fake.posts("settings"), [{"enabled": True}])

        self.page.evaluate("() => refreshStatus()")  # the next status check doesn't bring it back, nor a new start
        self.page.reload()
        self.ready()
        self.assertFalse(self.page.is_visible("#updAsk"))
        self.open_tab()
        self.assertTrue(self.page.is_checked("#updAuto"))
        self.assertEqual(self.page.inner_text("#updWhen"), "Not checked yet")

    def test_the_first_start_question_no(self):
        fake = self.fake(ask=True, enabled=None, checked=None)
        self.open()
        self.page.wait_for_selector("#updAsk[open]")
        self.page.click("#updAskNo")
        self.page.wait_for_selector("#updAsk", state="hidden")
        self.assertEqual(fake.posts("settings"), [{"enabled": False}])
        self.page.wait_for_selector(".toast >> text=No automatic checks. Settings → Updates can check now")
        self.page.evaluate("() => refreshStatus()")
        self.assertFalse(self.page.is_visible("#updAsk"))
        self.open_tab()
        self.assertFalse(self.page.is_checked("#updAuto"))
        self.assertIn("Automatic checks are off. Check now asks GitHub once.", self.text("#set-panel .set-row:nth-child(3)"))

    def test_the_question_put_away_comes_back_at_the_next_start(self):
        fake = self.fake(ask=True, enabled=None, checked=None)
        self.open()
        self.page.wait_for_selector("#updAsk[open]")
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector("#updAsk", state="hidden")
        self.page.evaluate("() => refreshStatus()")
        self.assertFalse(self.page.is_visible("#updAsk"), "not again on this page")
        self.assertEqual(fake.posts("settings"), [], "not an answer")
        self.page.reload()
        self.ready()
        self.page.wait_for_selector("#updAsk[open]")

    # ---- the pill and the notice -------------------------------------------------------------------------------
    def test_the_pill(self):
        fake = self.fake(available=release())
        self.open()
        pill = self.page.locator("#topStatus [data-open-settings=updates]")
        self.assertEqual(pill.inner_text(), "Update available · 0.3.0")
        self.assertIn("accent", pill.get_attribute("class").split())
        pill.click()
        self.page.wait_for_selector("#settings[open] #tab-updates[aria-selected=true]")
        self.page.wait_for_selector("#set-panel .upd-card")
        self.assertEqual(self.page.inner_text("#tab-updates small"), "New")
        self.page.keyboard.press("Escape")

        fake.state.update(channel="latest", available=changes())
        self.page.evaluate("() => refreshStatus()")
        self.assertEqual(pill.inner_text(), "12 new changes")
        fake.state["available"] = None
        self.page.evaluate("() => refreshStatus()")
        self.assertEqual(pill.count(), 0, "nothing new, no pill")

    def test_the_notice_once(self):
        fake = self.fake(available=release())
        self.open()
        notice = self.page.locator("#updNotice")
        notice.wait_for()
        self.assertEqual(self.text("#updNotice .upd-notice-text"), "Sedjem 0.3.0 is out")
        self.assertEqual(self.texts("#updNotice button"), ["Later", "What's new"])
        self.assertEqual(self.stored("sedjem-update-noticed"), "0.3.0")
        self.page.click("#updLater")
        notice.wait_for(state="detached")
        self.assertTrue(self.page.is_visible("#topStatus [data-open-settings=updates]"), "the pill stays")

        self.page.reload()
        self.ready()
        self.assertEqual(notice.count(), 0, "not again after a reload")

        fake.state["available"] = release(version="0.3.1", title="Sedjem 0.3.1")  # a newer one: once more
        self.page.reload()
        self.ready()
        notice.wait_for()
        self.assertEqual(self.text("#updNotice .upd-notice-text"), "Sedjem 0.3.1 is out")
        self.page.click("#updWhatsNew")
        self.page.wait_for_selector("#settings[open] #tab-updates[aria-selected=true]")
        self.page.wait_for_selector("#set-panel .upd-card-head:has-text('Sedjem 0.3.1')")
        self.assertEqual(notice.count(), 0)

        fake.state["available"] = release(version="0.4.0", title="Sedjem 0.4.0", skipped=True)  # skipped: nothing
        self.page.reload()
        self.ready()
        self.page.wait_for_timeout(300)
        self.assertEqual(notice.count(), 0, "not for a skipped one")
        self.assertEqual(self.page.locator("#topStatus [data-open-settings=updates]").count(), 0)
        self.assertEqual(self.stored("sedjem-update-noticed"), "0.3.1")

        fake.state.update(channel="latest", available=changes())  # the Latest channel's words
        self.page.reload()
        self.ready()
        notice.wait_for()
        self.assertEqual(self.text("#updNotice .upd-notice-text"), "12 new changes on main")
        self.assertEqual(self.stored("sedjem-update-noticed"), sha(TITLES[0]))

    def test_seen_in_the_tab_means_no_notice(self):
        fake = self.fake()
        self.open()
        self.open_tab()
        fake.check = lambda s: s.update(checked=ago(seconds=1), found="update", available=release())
        self.page.click("#updCheck")
        self.page.wait_for_selector("#set-panel .upd-card")
        self.page.keyboard.press("Escape")  # the status check as Settings closes finds it: already seen
        self.page.wait_for_selector("#topStatus [data-open-settings=updates]")
        self.page.wait_for_function("() => !UPD.loading")
        self.assertEqual(self.page.locator("#updNotice").count(), 0)

    # ---- Settings → Updates ----------------------------------------------------------------------------------
    def test_the_tab_on_stable(self):
        self.fake(available=release())
        self.open()
        self.page.locator("#updNotice").wait_for()
        self.open_tab()
        self.assertEqual(self.page.locator("#updNotice").count(), 0, "seen in the tab, the notice goes")
        self.assertEqual(self.texts("#set-panel .upd-running .about-text > *"),
                         ["Sedjem 0.2.0", f"commit {sha('v0.2.0')[:7]} · Oct 19, 2026", "Runs from the source code"])
        self.assertEqual(self.texts("#set-panel .set-row .set-text > b"),
                         ["Check for updates automatically", "Channel", "Checked 2 hours ago"])
        self.assertIn("GitHub sees this computer's IP address; nothing else is sent.", self.text("#set-panel label.set-row"))
        self.assertTrue(self.page.is_checked("#updAuto"))
        self.assertEqual(self.texts("#set-panel [data-upd-channel]"), ["Stable releases", "Latest changes"])
        self.assertEqual(self.page.get_attribute("#updChannel-stable", "aria-pressed"), "true")
        self.assertEqual(self.page.inner_text("#updCheck"), "Check now")

        self.assertEqual(self.page.inner_text("#upd-new"), "What's new")
        self.assertEqual(self.texts("#set-panel .upd-card-head .set-text > *"), ["Sedjem 0.3.0", "Released Oct 21, 2026"])
        self.assertEqual(self.texts("#set-panel .upd-actions > *"), ["Update now", "Download", "Skip this version"])
        download = self.page.locator("#updDownload")
        self.assertEqual((download.get_attribute("href"), download.get_attribute("target")), (f"{REPO}/releases/tag/v0.3.0", "_blank"))

        notes = "#set-panel .upd-notes"
        self.assertEqual(self.texts(f"{notes} h5"), ["Download", "Updates"])
        self.assertEqual(self.texts(f"{notes} > ul > li > code"), ["Sedjem-0.3.0-setup.exe", "Sedjem-0.3.0-linux-x64.tar.gz", "Sedjem/Sedjem"])
        self.assertIn("It isn't signed yet, so SmartScreen warns: choose More info, then Run anyway.",
                      self.texts(f"{notes} > ul > li")[0], "a wrapped line belongs to its item")
        self.assertEqual(self.texts(f"{notes} li li"), ["SHA256SUMS.txt has the checksums of both."])
        self.assertEqual(self.texts(f"{notes} em"), ["More info", "Run anyway"])
        self.assertEqual(self.texts(f"{notes} ol strong"), ["Stable releases", "Latest changes"])
        self.assertEqual(self.texts(f"{notes} pre"), ["git pull --ff-only"])
        links = self.page.eval_on_selector_all(f"{notes} a", "as => as.map(a => [a.textContent, a.getAttribute('href'), a.target, a.rel])")
        self.assertEqual(links, [
            ["the docs", f"{REPO}/tree/main/docs", "_blank", "noopener noreferrer"],
            [f"{REPO}/compare/v0.2.0...v0.3.0", f"{REPO}/compare/v0.2.0...v0.3.0", "_blank", "noopener noreferrer"]])

    def test_the_tab_on_latest(self):
        preview = running(version="0.2.1-preview.20261019", kind="windows", build_channel="preview")
        fake = self.fake(running=preview, available=release(action="installer"))
        fake.offers = {"latest": changes(action="installer", version="0.2.1-preview.20261021"),
                       "stable": release(action="installer")}
        self.open()
        self.open_tab()
        self.assertEqual(self.texts("#set-panel .upd-running .about-text > *"), [
            "Sedjem 0.2.1-preview.20261019", f"Preview build · commit {sha('v0.2.0')[:7]} · Oct 19, 2026",
            "Installed with the Windows installer"])
        self.assertIn("nightly preview builds of main", self.text("#set-panel .set-row:nth-child(2)"))

        self.page.click("#updChannel-latest")
        self.page.wait_for_selector(".toast >> text=Saved")
        self.assertEqual(fake.posts("settings"), [{"channel": "latest"}])
        self.page.wait_for_selector("#set-panel .upd-changes")
        self.assertEqual(self.page.get_attribute("#updChannel-latest", "aria-pressed"), "true")
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "updChannel-latest", "the focus stays")
        self.assertEqual(self.page.inner_text("#set-panel .upd-card-head b"), "12 new changes")
        self.assertTrue(self.text("#set-panel .upd-card-head .hint").startswith("Build 0.2.1-preview.20261021 · newest change "))
        self.assertEqual(self.texts("#set-panel .upd-change-title"), TITLES)
        rows = self.page.eval_on_selector_all("#set-panel .upd-changes li", """lis => lis.map(li => {
            const a = li.querySelector('a'), t = li.querySelector('time');
            return [a.textContent, a.getAttribute('href'), a.target, a.rel, !!t.dateTime && t.textContent.length > 0]; })""")
        self.assertEqual(rows, [[sha(t)[:7], f"{REPO}/commit/{sha(t)}", "_blank", "noopener noreferrer", True] for t in TITLES])
        self.assertEqual(self.page.inner_text("#set-panel .upd-more"), "and 7 more")
        self.assertEqual(self.page.get_attribute("#set-panel .upd-more a", "href"), fake.offers["latest"]["url"])
        self.assertEqual(self.page.locator("#set-panel .upd-notes").count(), 0)
        self.page.wait_for_selector("#topStatus [data-open-settings=updates]:has-text('12 new changes')")

        self.page.click("#updChannel-stable")  # back: the release notes again
        self.page.wait_for_selector("#set-panel .upd-notes")
        self.page.locator("#updAuto").uncheck()
        self.page.wait_for_function("() => document.querySelectorAll('.toast').length && !UPD.data.enabled")
        self.assertEqual(fake.posts("settings"), [{"channel": "latest"}, {"channel": "stable"}, {"enabled": False}])
        self.assertFalse(self.page.is_checked("#updAuto"))

    def test_check_now_and_its_errors(self):
        fake = self.fake(checked=ago(days=3))
        fake.hold.add("check")
        self.open()
        self.open_tab()
        self.assertEqual(self.page.inner_text("#updWhen"), "Checked 3 days ago")
        self.assertEqual(self.text("#set-panel .upd-empty"), "Sedjem is up to date: no release is newer than 0.2.0.")

        def check(outcome, shown):
            fake.check = outcome
            self.page.click("#updCheck")
            self.page.wait_for_selector("#updCheck[aria-disabled=true]:has-text('Checking…')")
            self.page.eval_on_selector("#updCheck", "b => b.click()")  # busy: a second click asks nothing more
            fake.release("check")
            self.page.wait_for_selector("#updCheck:not([aria-disabled]):has-text('Check now')")
            self.assertEqual(self.page.inner_text("#updWhen"), shown)

        check(lambda s: s.update(error="offline"), "Couldn't check (offline)")
        self.assertIn("Last checked 3 days ago", self.text("#set-panel .set-row:nth-child(3) .set-text"))
        self.assertEqual(self.page.eval_on_selector("#updWhen", "e => getComputedStyle(e).color"), "rgb(180, 35, 27)")
        check(lambda s: s.update(error="rate limited"), "Couldn't check (GitHub's hourly limit was reached; try again later)")
        check(lambda s: s.update(error="GitHub answered 502"), "Couldn't check: GitHub answered 502")
        self.assertEqual(len(fake.posts("check")), 3)
        self.assertEqual(self.page.locator("#topStatus [data-open-settings=updates]").count(), 0)

        check(lambda s: s.update(error=None, checked=ago(seconds=2), found="update", available=release()), "Checked just now")
        self.page.wait_for_selector("#set-panel .upd-card-head:has-text('Sedjem 0.3.0')")
        self.page.wait_for_selector("#topStatus [data-open-settings=updates]:has-text('Update available · 0.3.0')")

        check(lambda s: s.update(checked=ago(seconds=1), found="ahead", available=None,
                                 note="This checkout has 2 commits that main doesn't have."), "Checked just now")
        self.assertIn("This checkout has 2 commits that main doesn't have.", self.text("#set-panel .set-row:nth-child(3)"))
        self.assertEqual(self.text("#set-panel .upd-empty"), "Nothing newer to update to.")

    def test_skip_this_version(self):
        fake = self.fake(available=release())
        self.open()
        self.open_tab()
        pill = self.page.locator("#topStatus [data-open-settings=updates]")
        self.page.click("#updSkip")
        self.page.wait_for_selector(".toast >> text=Skipped. Sedjem tells you again when something newer is out")
        self.assertEqual(fake.posts("skip"), [{"skip": True}])
        self.assertEqual(self.page.inner_text("#set-panel .upd-card-head .status"), "Skipped")
        self.assertEqual(self.page.inner_text("#updSkip"), "Don't skip")
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "updSkip", "the focus stays on the button")
        pill.wait_for(state="detached")

        self.page.click("#updSkip")
        self.page.wait_for_selector(".toast >> text=No longer skipped")
        self.assertEqual(fake.posts("skip"), [{"skip": True}, {"skip": False}])
        self.assertEqual(self.page.inner_text("#updSkip"), "Skip this version")
        self.assertEqual(self.page.locator("#set-panel .upd-card-head .status").count(), 0)
        pill.wait_for()

    def test_update_now_blocked_gives_the_commands(self):
        commands = ['cd "/home/you/sedjem"', "git stash", "git pull --ff-only origin main",
                    ".venv/bin/python -m pip install -r requirements.txt"]
        self.fake(available=release(blocked="This checkout has changes that aren't committed.", commands=commands))
        self.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.base)
        self.open()
        self.open_tab()
        self.assertEqual(self.page.locator("#updInstall").count(), 0, "no Update now")
        self.assertEqual(self.texts("#set-panel .upd-actions > *"), ["Download", "Skip this version"])
        self.assertEqual(self.page.inner_text("#set-panel .upd-blocked b"), "This checkout has changes that aren't committed.")
        self.assertEqual(self.page.eval_on_selector_all("#set-panel .upd-cmd pre", "els => els.map(e => e.textContent)"), commands)
        self.assertEqual(self.texts("#set-panel [data-upd-copy]"), ["Copy"] * 4)
        self.page.click("#set-panel [data-upd-copy='2']")
        self.page.wait_for_selector(".toast >> text=Command copied")
        self.assertEqual(self.page.evaluate("() => navigator.clipboard.readText()"), commands[2])

    # ---- Update now ----------------------------------------------------------------------------------------
    def test_update_now_through_the_restart(self):
        fake = self.fake(running=running(kind="windows"), available=release(action="installer"))
        self.open()
        self.open_tab()
        asked = []
        self.page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
        self.page.click("#updInstall")
        self.page.wait_for_selector("#updProgress")
        self.assertIn("Sedjem downloads its installer from GitHub, checks it against the release's checksums and runs it.", asked[0])
        self.assertIn("opens again when it's done", asked[0])
        self.assertEqual(len(fake.posts("install")), 1)
        self.assertEqual(self.page.locator("#set-panel .upd-actions").count(), 0, "the progress instead of the buttons")

        fake.state["install"] = install("downloading", done=12_000_000)
        self.page.wait_for_selector("#updProgressText:has-text('12 MB of 48 MB')")
        self.assertEqual(self.page.inner_text("#updProgress b"), "Downloading")
        self.page.wait_for_function("() => document.querySelector('#updProgress .progress i').style.width === '25%'")
        fake.state["install"] = install("verifying", done=48_000_000)
        self.page.wait_for_selector("#updProgress b:has-text('Checking the download')")
        self.assertIn("indeterminate", self.page.get_attribute("#updProgress .progress", "class"))
        fake.state["install"] = install("installing", done=48_000_000)
        self.page.wait_for_selector("#updProgress b:has-text('Running the installer')")
        self.assertTrue(self.stored("sedjem-update-from"), "what ran before, to say what it was updated to")

        self.page.evaluate("() => (window.before = true)")
        with self.page.expect_event("load", timeout=20_000):
            fake.down = 2  # away for two checks, then the new one answers
            fake.state["install"] = install("restarting", done=48_000_000)
            self.page.wait_for_selector("#updRestart[open]")
            self.assertEqual(self.page.inner_text("#updRestartTitle"), "Sedjem is restarting…")
            self.assertIn("The installer is updating it", self.page.inner_text("#updRestartText"))
            fake.state.update(running=running(version="0.3.0", commit=sha("v0.3.0"), kind="windows"), available=None,
                              install=None, found="up to date")
        self.ready()
        self.assertIsNone(self.page.evaluate("() => window.before ?? null"), "the page was loaded again")
        self.page.wait_for_selector(".toast >> text=Updated to 0.3.0")
        self.assertIsNone(self.stored("sedjem-update-from"))
        self.assertEqual(fake.down, 0)
        self.assertEqual(len([e for e in self.errors if e.startswith(REFUSED)]), 2)
        self.errors = [e for e in self.errors if not e.startswith(REFUSED)]

    def test_restarting_from_source_to_the_latest_changes(self):
        fake = self.fake(channel="latest", available=changes())
        self.open()
        self.open_tab()
        asked = []
        self.page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
        self.page.click("#updInstall")
        self.page.wait_for_selector("#updProgress b:has-text('Fetching the changes')")
        self.assertIn(f"Update to the newest changes on main ({sha(TITLES[0])[:7]})? Sedjem runs git pull", asked[0])
        self.page.evaluate("() => (window.before = true)")
        with self.page.expect_event("load", timeout=20_000):
            fake.state["install"] = install("restarting")  # the new one comes up between two checks
            self.page.wait_for_selector("#updRestart[open]")
            fake.state.update(running=running(commit=sha(TITLES[0])), available=None, install=None)
        self.ready()
        self.page.wait_for_selector(f".toast >> text=Updated to the latest changes ({sha(TITLES[0])[:7]})")

    def test_a_failed_update_and_try_again(self):
        fake = self.fake(available=release())
        fake.refuse = {"install": "2 transcriptions are running; Sedjem restarts to update."}
        self.open()
        self.open_tab()
        self.page.once("dialog", lambda d: d.accept())
        self.page.click("#updInstall")
        self.page.wait_for_selector(".toast.error >> text=2 transcriptions are running; Sedjem restarts to update.")
        self.assertIsNone(self.stored("sedjem-update-from"), "nothing started")
        self.errors = [e for e in self.errors if not e.startswith(CONFLICT)]

        fake.refuse = {}
        self.page.once("dialog", lambda d: d.accept())
        self.page.click("#updInstall")
        self.page.wait_for_selector("#updProgress b:has-text('Fetching the changes')")
        fake.state["install"] = install("failed", error="git fetch: could not resolve host github.com")
        self.page.wait_for_selector("#set-panel .upd-failed")
        self.assertEqual(self.text("#set-panel .upd-failed .set-text"), "The update failed git fetch: could not resolve host github.com")
        self.assertEqual(self.page.locator("#updInstall").count(), 0, "Try again instead")
        self.assertIsNone(self.stored("sedjem-update-from"), "no restart to report")
        self.page.click("#updRetry")  # asked once already: no question this time
        self.page.wait_for_selector("#updProgress")
        self.assertEqual(len(fake.posts("install")), 3)

    def test_a_running_transcription_waits_for_the_restart(self):
        fake = self.fake(available=release())
        self.open()
        self.open_tab()
        self.page.evaluate("() => { S.jobs[0].status = 'running'; }")
        self.page.click("#updInstall")
        self.page.wait_for_selector(".toast.error >> text=1 transcription is still running. Sedjem restarts to update, so wait until it finishes or stop it first.")
        self.assertEqual(fake.posts("install"), [])

    def test_what_update_now_says_for_each_kind(self):
        fake = self.fake(running=running(kind="linux"), available=release())
        self.open()
        self.open_tab()
        for action, words in (("git", "Sedjem runs git pull to move this checkout forward to it (fast-forward only), installs the requirements again if they changed, and restarts."),
                              ("installer", "Windows SmartScreen may warn about the installer, since it isn't signed yet."),
                              ("archive", "Download Sedjem 0.3.0? Sedjem downloads the Linux archive from GitHub, checks it against the release's checksums and unpacks it beside this one, then shows the folder.")):
            fake.state["available"]["action"] = action
            self.page.evaluate("() => loadUpdates()")
            asked = []
            self.page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
            self.page.click("#updInstall")
            self.assertIn(words, asked[0])
        self.assertEqual(fake.posts("install"), [], "nothing starts without a yes")

        self.page.once("dialog", lambda d: d.accept())  # the Linux build: unpacked beside this one, no restart
        self.page.click("#updInstall")
        self.page.wait_for_selector("#updProgress b:has-text('Downloading')")
        self.assertIsNone(self.stored("sedjem-update-from"))
        fake.state["install"] = install("done", done=48_000_000, folder="/home/you/Apps/Sedjem-0.3.0")
        self.page.wait_for_selector("#set-panel .upd-done")
        self.assertEqual(self.text("#set-panel .upd-done"), "Sedjem 0.3.0 is unpacked in /home/you/Apps/Sedjem-0.3.0, which "
                         "opened in the file manager. Quit this one and start the new one from there.")
        self.assertEqual(self.texts("#set-panel .upd-actions > *"), ["Download", "Skip this version"])

        fake.state.update(install=None)
        fake.state["available"]["action"] = "download"  # an install Sedjem can't update: Download only
        self.page.evaluate("() => loadUpdates()")
        self.page.wait_for_selector("#set-panel .upd-done", state="detached")
        self.assertEqual(self.texts("#set-panel .upd-actions > *"), ["Download", "Skip this version"])

    def test_the_restart_gives_up_after_two_minutes(self):
        fake = self.fake(available=release())
        self.page.clock.install()
        self.open()
        self.open_tab()
        self.page.once("dialog", lambda d: d.accept())
        self.page.click("#updInstall")
        self.page.wait_for_selector("#updProgress")
        fake.down = 10_000
        fake.state["install"] = install("restarting")
        self.page.clock.run_for(1100)  # the next look at the progress
        self.page.wait_for_selector("#updRestart[open]")
        self.page.keyboard.press("Escape")
        self.assertTrue(self.page.is_visible("#updRestart"), "it can't be put away")
        self.page.clock.run_for(125_000)
        self.page.wait_for_selector("#updRestartTitle:has-text(\"Sedjem didn't come back\")")
        self.assertEqual(self.page.inner_text("#updRestartText"), "It hasn't answered for two minutes. Start it again with "
                         "./app.sh in the project folder, then reload this page.")
        self.assertTrue(self.page.is_visible("#updReload"))
        self.assertFalse(self.page.is_visible("#updRestartBar"))
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "updReload")
        self.errors = [e for e in self.errors if not e.startswith(REFUSED)]

    # ---- safety, the dark theme, a narrow window -------------------------------------------------------------
    def test_release_notes_and_changes_are_text_not_markup(self):
        latest = changes(ahead=2)
        latest["changes"] = [{"sha": sha("a"), "title": "<img src=x onerror=\"window.__pwned = 5\"> <b>loud</b>",
                              "date": ago(hours=1), "url": "javascript:window.__pwned = 6"},
                             {"sha": sha("b"), "title": "Fine", "date": ago(hours=2), "url": f"{REPO}/commit/{sha('b')}"}]
        fake = self.fake(available=release(notes=HOSTILE, url="javascript:window.__pwned = 7"))
        fake.offers = {"latest": latest}
        self.open()
        self.open_tab()
        notes = self.page.locator("#set-panel .upd-notes")
        self.assertEqual(notes.locator("script, img, b, i, iframe").count(), 0)
        shown = notes.inner_text()
        for text in ("<script>window.__pwned = 1</script>", '<img src=x onerror="window.__pwned = 2"> and <b>bold</b> <i>italic</i>',
                     "[click](javascript:window.__pwned=3)", '<iframe src="https://example.com">'):
            self.assertIn(text, shown)
        links = notes.locator("a").evaluate_all("as => as.map(a => [a.getAttribute('href'), a.target, a.rel])")
        self.assertEqual(links, [['https://example.com/"><script>window.__pwned=4</script>', "_blank", "noopener noreferrer"]])
        self.assertEqual(self.page.locator("#updDownload").count(), 0, "no link to an address that isn't https")

        self.page.click("#updChannel-latest")
        self.page.wait_for_selector("#set-panel .upd-changes")
        self.assertEqual(self.page.locator("#set-panel .upd-changes img, #set-panel .upd-changes b").count(), 0)
        self.assertEqual(self.texts("#set-panel .upd-change-title")[0], '<img src=x onerror="window.__pwned = 5"> <b>loud</b>')
        self.assertEqual(self.page.eval_on_selector_all("#set-panel .upd-changes a", "as => as.map(a => a.getAttribute('href'))"),
                         [f"{REPO}/commit/{sha('b')}"])
        self.page.wait_for_timeout(200)
        self.assertIsNone(self.page.evaluate("() => window.__pwned ?? null"))

    def test_dark_and_narrow(self):
        commands = ['cd "/home/you/projects/a-folder-with-a-rather-long-name/sedjem"', "git pull --ff-only origin main"]
        fake = self.fake(available=release(blocked="This checkout is on another branch (my-experiments), so it can't move forward.",
                                           commands=commands, notes=NOTES + "\nhttps://github.com/" + "a-very-long-path/" * 8))
        fake.offers = {"latest": changes()}
        self.page.set_viewport_size({"width": 390, "height": 800})
        self.page.emulate_media(color_scheme="dark")
        self.open()
        self.assertEqual(self.page.evaluate("() => document.documentElement.dataset.theme"), "dark")
        box = self.page.locator("#updNotice").bounding_box()
        self.assertTrue(0 <= box["x"] and box["x"] + box["width"] <= 390, f"the notice fits: {box}")
        self.assertEqual(self.page.eval_on_selector("#updNotice", "e => getComputedStyle(e).backgroundColor"), "rgb(22, 26, 32)")
        self.open_tab()
        tab = self.page.locator("#tab-updates").bounding_box()
        self.assertTrue(0 <= tab["x"] and tab["x"] + tab["width"] <= 390, f"the tab stays in view: {tab}")
        wide = """() => [document.documentElement.scrollWidth - innerWidth,
            ...[...document.querySelectorAll('#set-panel, #set-panel .set-row, #set-panel .upd-card, #set-panel .upd-notes, #set-panel pre, #set-panel .upd-changes li, #set-panel .upd-blocked')]
                .map((e) => e.scrollWidth - e.clientWidth)]"""
        self.assertLessEqual(max(self.page.evaluate(wide)), 0, "no sideways scrolling")
        colours = self.page.evaluate("""() => ['.upd-card', '.upd-cmd', '.set-row']
            .map((s) => getComputedStyle(document.querySelector('#set-panel ' + s)).backgroundColor)""")
        self.assertEqual(colours, ["rgb(22, 26, 32)", "rgb(22, 26, 32)", "rgb(22, 26, 32)"], "the dark theme's surfaces")
        self.page.click("#updChannel-latest")
        self.page.wait_for_selector("#set-panel .upd-changes")
        self.assertLessEqual(max(self.page.evaluate(wide)), 0, "no sideways scrolling in the list of changes")


if __name__ == "__main__":
    unittest.main()
