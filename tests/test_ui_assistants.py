"""Browser tests of assistants over MCP in the app (app/static/assistants.js): Settings → Assistants with its two
switches (kept after a reload), what is hidden one by one and Show again, the four connection snippets and Copy,
and what assistants asked (written here with assistants.log, as app/mcp.py does) and Clear; hiding a transcription
from its page and a course from a lecture's course bar; the dark theme and a narrow window. Headless Chromium
against a Sedjem server with demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_assistants.py" -v
"""
import unittest

from ui_support import UiTestCase  # (puts the project on the path)

from app import assistants

MEETING, INTRO, LOOPS, TALK = "20261019-100000-a001", "20261019-110000-c001", "20261019-110000-c002", "20261019-090000-d001"
GONE = "20261001-090000-eeee"  # a transcription deleted since
COURSE = {"id": "PLpython", "title": "Python for beginners", "url": "https://www.youtube.com/playlist?list=PLpython",
          "count": 3, "folder": "Python for beginners"}
LECTURE = lambda n: {"course": {**COURSE, "index": n}, "youtube_id": f"vidpython0{n}",
                     "source_url": f"https://www.youtube.com/watch?v=vidpython0{n}", "created": "2026-10-19T11:00:00+03:00"}
CLIENTS = ["Claude Desktop", "Claude Code", "Cursor", "VS Code"]


class Assistants(UiTestCase):
    jobs = ({"jid": MEETING, "title": "Sprint planning", "created": "2026-10-19T10:00:00+03:00"},
            {"jid": INTRO, "title": "Introduction", **LECTURE(1)},
            {"jid": LOOPS, "title": "Loops", **LECTURE(2)},
            {"jid": TALK, "title": "A talk on its own", "youtube_id": "vidalone001",
             "source_url": "https://www.youtube.com/watch?v=vidalone001", "created": "2026-10-19T09:00:00+03:00"})

    def setUp(self):
        super().setUp()
        self.cfg = self.app.cfg  # each test starts with everything visible and nothing in the log
        self.cfg.save_settings(mcp_courses=True, mcp_recordings=True, mcp_hidden=[])
        assistants.clear_log(self.cfg)

    def open_tab(self):
        self.open_settings("assistants")
        self.page.wait_for_selector("#settings .ai-snippet")

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())")

    def job_page(self, jid):
        self.open(f"#/job/{jid}")
        self.page.wait_for_selector("#aiPanel")

    def test_the_tab(self):
        self.open()
        self.open_tab()
        self.assertIn("Claude Desktop, Claude Code, Cursor and VS Code can read and search",
                      self.page.inner_text("#settings .ai-intro"))
        self.assertEqual(self.texts("#settings .ai-sec h4"),
                         ["What assistants can see", "Hidden from assistants", "Connect an assistant", "What assistants asked"])
        self.assertEqual(self.texts("#settings .ai-sec .set-row .set-text b"),
                         ["Courses and YouTube videos", "Meetings and other recordings"])
        self.assertTrue(self.page.is_checked("#settings [data-setting=mcp_courses]"))
        self.assertTrue(self.page.is_checked("#settings [data-setting=mcp_recordings]"))
        self.assertIn("Nothing is hidden", self.page.inner_text("#settings .ai-empty >> nth=0"))
        snippets = assistants.snippets(self.cfg)
        self.assertEqual(self.texts("#settings .ai-snippet-head b"), CLIENTS)
        self.assertEqual(self.page.eval_on_selector_all("#settings .ai-code", "els => els.map(e => e.textContent)"),
                         [s["text"] for s in snippets])
        self.assertEqual(self.texts("#settings .ai-snippet-head .hint")[1], "Run it in a terminal")
        self.assertEqual(self.texts("#settings [data-ai-copy]"), ["Copy"] * 4)
        self.assertTrue(self.page.is_disabled("#aiClear"), "nothing to clear")
        self.assertIn("Nothing yet", self.page.inner_text("#settings .ai-sec:last-child .ai-empty"))

    def test_the_switches_are_kept(self):
        self.open()
        self.open_tab()
        self.page.locator("#settings [data-setting=mcp_recordings]").uncheck()
        self.page.wait_for_selector(".toast >> text=Saved")
        self.assertFalse(self.api("GET", "/api/status")["json"]["settings"]["mcp_recordings"])
        self.assertEqual(self.page.evaluate("() => document.activeElement.dataset.setting"), "mcp_recordings",
                         "the switch keeps the focus when Settings is drawn again")
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.open_tab()
        self.assertFalse(self.page.is_checked("#settings [data-setting=mcp_recordings]"))
        self.assertTrue(self.page.is_checked("#settings [data-setting=mcp_courses]"))

        # a meeting's page says why assistants can't read it, and leads to the switch
        self.page.keyboard.press("Escape")
        self.job_page(MEETING)
        self.assertIn("Hidden from assistants: Meetings and other recordings are switched off in Settings.",
                      self.page.inner_text("#aiPanel"))
        self.assertEqual(self.page.locator("#aiToggle").count(), 0, "its own mark changes nothing while the switch is off")
        self.page.click("#aiPanel [data-open-settings=assistants]")
        self.page.wait_for_selector("#settings[open] #tab-assistants[aria-selected=true]")
        self.page.locator("#settings [data-setting=mcp_recordings]").check()
        self.page.wait_for_selector(".toast >> text=Saved")
        self.page.keyboard.press("Escape")  # the page follows when Settings closes
        self.page.wait_for_selector("#aiPanel:has-text('Visible to assistants connected to Sedjem.')")
        self.assertTrue(self.cfg.settings()["mcp_recordings"])

    def test_hiding_from_a_page_and_from_a_course_bar(self):
        self.job_page(MEETING)
        self.assertIn("Visible to assistants connected to Sedjem.", self.page.inner_text("#aiPanel"))
        self.assertEqual(self.page.locator(".course-bar").count(), 0)
        self.page.click("#aiToggle")
        self.page.wait_for_selector(".toast >> text=Hidden from assistants")
        self.assertIn("Hidden from assistants.", self.page.inner_text("#aiPanel"))
        self.assertEqual(self.page.inner_text("#aiToggle"), "Show to assistants")
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "aiToggle", "the focus stays on the button")
        self.assertEqual(self.cfg.setting("mcp_hidden"), [f"job:{MEETING}"])

        self.job_page(TALK)  # a video on its own has no course to hide
        self.assertEqual((self.page.locator(".course-bar").count(), self.page.locator("#aiCourse").count()), (1, 0))

        self.job_page(LOOPS)
        self.assertEqual(self.page.inner_text(".course-bar #aiCourse"), "Hide course from assistants")
        self.page.click("#aiCourse")
        self.page.wait_for_selector(".toast >> text=Course hidden from assistants")
        self.assertEqual(self.page.inner_text("#aiCourse"), "Show course to assistants")
        self.assertIn("Hidden from assistants with its course.", self.page.inner_text("#aiPanel"))
        self.assertEqual(self.page.inner_text("#aiCourseShow"), "Show course to assistants")

        self.open_tab()
        self.assertEqual(self.texts("#settings .ai-hidden .set-item-text"),
                         ["Python for beginners 2 of 3 lectures", "Sprint planning Recording, 19 Oct 2026"])
        self.page.click("#settings [data-ai-show='course:PLpython']")
        self.page.wait_for_selector(".toast >> text=Course shown to assistants")
        self.assertEqual(self.texts("#settings .ai-hidden .set-item-text b"), ["Sprint planning"])
        self.assertEqual(self.page.evaluate("() => document.activeElement.dataset.aiShow"), f"job:{MEETING}",
                         "the focus goes on to the next one")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.page.inner_text("#aiCourse"), "Hide course from assistants", "the page behind followed")
        self.assertIn("Visible to assistants", self.page.inner_text("#aiPanel"))

        self.open_tab()
        self.page.click("#settings [data-ai-show]")
        self.page.wait_for_selector("#settings .ai-empty:has-text('Nothing is hidden')")
        self.assertEqual(self.cfg.setting("mcp_hidden"), [])

    def test_a_deleted_transcription_is_shown_by_its_key(self):
        assistants.set_hidden(self.cfg, f"job:{GONE}", True)
        self.open()
        self.open_tab()
        self.assertEqual(self.texts("#settings .ai-hidden .set-item-text"), [f"job:{GONE} This transcription was deleted"])
        self.page.click(f"#settings [data-ai-show='job:{GONE}']")
        self.page.wait_for_selector("#settings .ai-empty:has-text('Nothing is hidden')")

    def test_what_assistants_asked(self):
        assistants.log(self.cfg, "Claude Desktop 1.2", "search", "tokenizers", [INTRO, LOOPS, MEETING, TALK, GONE])
        assistants.log(self.cfg, "Cursor", "get_transcript", "Sprint planning, 00:00-10:00", [MEETING])
        assistants.log(self.cfg, "Claude Code", "search", "<b>a query</b>")
        self.open()
        self.open_tab()
        self.assertEqual(self.texts("#settings .ai-log .ai-entry"), [
            "Claude Code Searched for <b>a query</b>",
            "Cursor Read Sprint planning, 00:00-10:00 Sprint planning",
            "Claude Desktop 1.2 Searched for tokenizers Introduction, Loops, Sprint planning and 2 more"])
        self.assertEqual(self.page.locator("#settings .ai-entry b").count(), 3, "what an assistant sent is text, never markup")
        self.assertTrue(all(t.startswith("Today ") for t in self.texts("#settings .ai-log time")))

        self.page.keyboard.press("Escape")  # what assistants ask while Settings is closed shows when it opens again
        assistants.log(self.cfg, "VS Code", "course_outline", "Python for beginners", [INTRO, LOOPS])
        self.open_tab()
        self.page.wait_for_selector("#settings .ai-log li:first-child:has-text('VS Code')")
        self.assertEqual(self.texts("#settings .ai-log li:first-child .ai-entry"),
                         ["VS Code Read the outline of Python for beginners Introduction, Loops"])
        # as plain text too (copied, or read without the layout): the words stay apart without CSS's line break
        self.assertEqual(self.page.eval_on_selector("#settings .ai-log li:first-child .ai-entry",
                                                    "e => e.textContent.replace(/\\s+/g, ' ').trim()"),
                         "VS Code Read the outline of Python for beginners Introduction, Loops")

        self.page.once("dialog", lambda d: d.dismiss())
        self.page.click("#aiClear")
        self.assertEqual(self.page.locator("#settings .ai-log li").count(), 4, "nothing is cleared without a yes")
        self.page.once("dialog", lambda d: d.accept())
        self.page.click("#aiClear")
        self.page.wait_for_selector(".toast >> text=Cleared")
        self.page.wait_for_selector("#settings .ai-empty:has-text('Nothing yet')")
        self.assertTrue(self.page.is_disabled("#aiClear"))
        self.assertEqual(assistants.read_log(self.cfg), [])

    def test_copy(self):
        self.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.base)
        self.open()
        self.open_tab()
        self.page.click("#settings [data-ai-copy='1']")
        self.page.wait_for_selector(".toast >> text=Copied for Claude Code")
        self.assertEqual(self.page.evaluate("() => navigator.clipboard.readText()"), assistants.snippets(self.cfg)[1]["text"])

    def test_copy_without_the_clipboard(self):
        # the clipboard refused (some browsers, the desktop window): a hidden text box and the copy command instead
        self.page.add_init_script("""navigator.clipboard.writeText = () => Promise.reject(new Error('not allowed'));
            window.__copied = [];
            document.addEventListener('copy', (e) => window.__copied.push(e.target.value));""")
        self.open()
        self.open_tab()
        self.page.click("#settings [data-ai-copy='0']")
        self.page.wait_for_selector(".toast >> text=Copied for Claude Desktop")
        self.assertEqual(self.page.evaluate("() => window.__copied"), [assistants.snippets(self.cfg)[0]["text"]])
        self.assertEqual(self.page.locator(".ai-copy-box").count(), 0, "the text box is gone")
        self.assertEqual(self.page.evaluate("() => document.activeElement.dataset.aiCopy"), "0", "the focus is back on Copy")

    def test_dark_and_narrow(self):
        assistants.log(self.cfg, "Claude Desktop 1.2", "search", "a long question " * 20, [INTRO, MEETING])
        assistants.set_hidden(self.cfg, f"job:{GONE}", True)
        assistants.set_hidden(self.cfg, "course:PLpython", True)
        self.page.set_viewport_size({"width": 390, "height": 800})
        self.page.emulate_media(color_scheme="dark")
        self.open()
        self.assertEqual(self.page.evaluate("() => document.documentElement.dataset.theme"), "dark")
        self.open_tab()
        tab = self.page.locator("#tab-assistants").bounding_box()
        self.assertTrue(0 <= tab["x"] and tab["x"] + tab["width"] <= 390, f"the tab stays in view: {tab}")
        wide = self.page.evaluate("""() => [document.documentElement.scrollWidth - innerWidth,
            ...[...document.querySelectorAll('#set-panel, #set-panel .ai-snippet, #set-panel .ai-code, #set-panel .ai-log li, #set-panel .set-item-row')]
                .map((e) => e.scrollWidth - e.clientWidth)]""")
        self.assertLessEqual(max(wide), 0, "no sideways scrolling")
        colours = self.page.evaluate("""() => ['.ai-snippet', '.ai-code', '.ai-log li']
            .map((s) => getComputedStyle(document.querySelector('#set-panel ' + s)).backgroundColor)""")
        self.assertEqual(colours, ["rgb(22, 26, 32)", "rgb(17, 21, 26)", "rgb(22, 26, 32)"], "the dark theme's surfaces")
        self.page.keyboard.press("Escape")
        self.job_page(LOOPS)
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - innerWidth"), 0)
        self.assertEqual(self.page.evaluate("() => getComputedStyle(document.getElementById('aiPanel')).backgroundColor"),
                         "rgb(22, 26, 32)")
        self.assertTrue(self.page.is_visible(".course-bar #aiCourse"))


if __name__ == "__main__":
    unittest.main()
