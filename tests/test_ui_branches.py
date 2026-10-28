"""Browser tests of a recording's main transcript and its branches (app/static/app.js, app/static/compare.js):
"New branch from this version" in History, with the name asked for in place, opening the new branch; its version 0
linked to where it came from; the job page's Branches menu marking the main transcript, which is then listed first
with a Main mark on the page and in the sidebar, opened by the recording's title, and taken as the compare view's
base; and a version saved by an assistant shown as such in History. Headless Chromium against a Sedjem server with
demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import unittest

from ui_support import LINES, UiTestCase

from app import mcp as sedjem_mcp
from app.config import Config

# Two recordings, each run with two models (the second a re-run of the first), and a recording on its own
A1, B1 = "20260926-100000-a1b1", "20260926-100100-b1c1"
A2, B2 = "20260926-090000-a2b2", "20260926-090100-b2c2"
SOLO = "20260926-080000-c3d3"
LECTURE, LECTURE_AGAIN = "20260926-070000-d1d1", "20260926-070100-d2d2"  # lecture 1 of a course, run twice
COURSE = {"id": "PLbranches01", "title": "Branches course", "url": "https://www.youtube.com/playlist?list=PLbranches01",
          "count": 2, "folder": "Branches course", "index": 1}
YT = {"youtube_id": "vidbranch01", "source_url": "https://www.youtube.com/watch?v=vidbranch01", "course": COURSE}
SECOND = [dict(x, start=x["start"] + 0.1, end=x["end"] + 0.1) for x in LINES]
SECOND[1]["text"] = "the project ده محتاج تركيز علشان الديدلاين قربت"
HOSTED = {"model": "elevenlabs", "model_title": "ElevenLabs Scribe v2", "kind": "hosted"}


class Branches(UiTestCase):
    jobs = ({"jid": A1, "title": "Planning", "created": "2026-09-26T10:00:00+03:00"},
            {"jid": B1, "title": "Planning", "lines": SECOND, "rerun_of": A1, "created": "2026-09-26T10:01:00+03:00", **HOSTED},
            {"jid": A2, "title": "Retro", "created": "2026-09-26T09:00:00+03:00"},
            {"jid": B2, "title": "Retro", "lines": SECOND, "rerun_of": A2, "created": "2026-09-26T09:01:00+03:00", **HOSTED},
            {"jid": SOLO, "title": "Solo call", "created": "2026-09-26T08:00:00+03:00"},
            {"jid": LECTURE, "title": "Lecture one", "created": "2026-09-26T07:00:00+03:00", **YT},
            {"jid": LECTURE_AGAIN, "title": "Lecture one", "lines": SECOND, "rerun_of": LECTURE,
             "created": "2026-09-26T07:01:00+03:00", **YT, **HOSTED})

    def setUp(self):
        super().setUp()
        self.dialogs = []  # nothing here may ask in a window of its own (window.prompt or confirm)
        self.page.on("dialog", lambda d: (self.dialogs.append(d.message), d.dismiss()))

    def tearDown(self):
        self.assertEqual(self.dialogs, [], "a dialog window was opened")
        super().tearDown()

    def job_page(self, jid):
        self.open(f"#/job/{jid}")
        self.page.wait_for_selector("#transcript .line .text")

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.trim())")

    def labels(self, selector):
        """The labels of a row of transcript pills, in order, with the one marked Main."""
        return self.page.eval_on_selector_all(selector, """els => els.map(e => (e.querySelector('.main-mark') ? 'Main: ' : '')
            + e.lastElementChild.innerText.trim())""")

    def test_a_new_branch_from_a_version_is_named_in_place_and_opens(self):
        self.open()
        fixed = [{**x, "text": "order ال promo code بسرعة"} if i == 0 else x for i, x in enumerate(LINES)]
        self.assertEqual(self.api("PATCH", f"/api/jobs/{A1}", {"lines": fixed, "message": "promo"})["json"]["version"], 1)
        self.job_page(A1)
        self.page.click("#historyBtn")
        self.page.wait_for_selector("#history[open] .ver")
        self.page.click("#history .ver[data-n='0'] [data-ver-branch='0']")
        field = self.page.wait_for_selector("#history .ver[data-n='0'] [data-ver-branch-form='0'] input")
        self.assertTrue(field.evaluate("e => e === document.activeElement"), "the name field has the focus")
        self.page.click("#history [data-ver-branch-form='0'] button[type='submit']")
        self.page.wait_for_selector(".toast >> text=Give the branch a name")
        self.page.fill("#history [data-ver-branch-form='0'] input", "suggestions")
        self.page.press("#history [data-ver-branch-form='0'] input", "Enter")

        self.page.wait_for_function(f"() => /^#\\/job\\//.test(location.hash) && location.hash !== '#/job/{A1}'")
        new = self.page.evaluate("() => location.hash.split('/').pop()")
        self.page.wait_for_selector(".toast >> text=Branch “suggestions” made from version 0")
        self.page.wait_for_selector("#history", state="hidden")
        self.page.wait_for_selector("#versionPill >> text=v0")
        self.assertEqual(self.texts("#transcript .line .text")[0], LINES[0]["text"], "version 0's text, not version 1's")
        self.assertEqual(self.labels("#groupBar .pill.ver"), ["Cohere Transcribe Arabic", "ElevenLabs Scribe v2", "suggestions"])
        self.assertEqual(self.texts("#groupBar .pill.ver.on"), ["suggestions"])
        self.assertEqual(self.labels("#jobList .job-item.grouped:has-text('Planning') .pill.ver"),
                         ["Cohere Transcribe Arabic", "ElevenLabs Scribe v2", "suggestions"])
        job = self.api("GET", f"/api/jobs/{new}")["json"]["job"]
        self.assertEqual(job["branch"], {"name": "suggestions", "from": {"id": A1, "version": 0}})

        # its history starts with the copy, linked to where it came from
        self.page.click("#historyBtn")
        row = self.page.wait_for_selector("#history .ver[data-n='0']")
        self.assertEqual(self.texts("#history .ver[data-n='0'] .ver-head .pill"), ["Branch", "Current"])
        self.assertEqual(self.page.inner_text("#history .ver[data-n='0'] .ver-origin"),
                         "Branched from Cohere Transcribe Arabic, version 0")
        self.assertEqual(row.eval_on_selector(".ver-origin a", "a => a.getAttribute('href')"), f"#/job/{A1}")

        # and the page's Branches menu asks for a branch of the version shown, in History
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector("#history", state="hidden")
        self.page.click("[data-menu='branchMenu']")
        self.page.click("#branchHere")
        self.page.wait_for_selector("#history[open] [data-ver-branch-form='0'] input")
        self.page.fill("#history [data-ver-branch-form='0'] input", "second pass")
        self.page.click("#history [data-ver-branch-form='0'] button[type='submit']")
        self.page.wait_for_selector(".toast >> text=Branch “second pass” made from version 0")
        self.page.wait_for_selector("#groupBar .pill.ver.on >> text=second pass")
        self.page.click("#historyBtn")
        self.page.click("#history .ver-origin a")  # the link leads to the branch it came from
        self.page.wait_for_url(f"**/#/job/{new}")
        self.page.wait_for_selector("#history", state="hidden")

    def test_the_main_transcript_is_chosen_from_a_menu_listed_first_and_opened_by_the_recording(self):
        self.job_page(A2)
        self.assertTrue(self.page.is_hidden("#mainPill"))
        self.assertEqual(self.labels("#groupBar .pill.ver"), ["Cohere Transcribe Arabic", "ElevenLabs Scribe v2"])
        sidebar = "#jobList .job-item.grouped:has-text('Retro')"
        self.assertEqual(self.page.get_attribute(f"{sidebar} a.t", "href"), f"#/job/{B2}", "without a main one: the newest")
        self.page.click("[data-menu='branchMenu']")
        self.assertEqual(self.page.inner_text("#mainBtn"), "Make this the main transcript")
        self.page.click("#mainBtn")
        self.page.wait_for_selector(".toast >> text=This is now the recording's main transcript")
        self.page.wait_for_selector("#mainPill", state="visible")
        self.page.wait_for_selector(f"{sidebar} a.t[href='#/job/{A2}']")
        self.assertEqual(self.labels(f"{sidebar} .pill.ver"), ["Main: Cohere Transcribe Arabic", "ElevenLabs Scribe v2"])

        # on the other transcript's page: listed after the main one, and Compare goes to the main one
        self.page.click("#groupBar .pill.ver >> text=ElevenLabs Scribe v2")
        self.page.wait_for_url(f"**/#/job/{B2}")
        self.page.wait_for_selector("#groupBar .pill.ver.on >> text=ElevenLabs Scribe v2")  # (its page, not the last one)
        self.assertEqual(self.labels("#groupBar .pill.ver"), ["Main: Cohere Transcribe Arabic", "ElevenLabs Scribe v2"])
        self.assertTrue(self.page.is_hidden("#mainPill"))
        self.assertEqual(self.page.get_attribute("#compareBtn", "href"), f"#/compare/{B2},{A2}")
        self.page.click("#compareBtn")
        self.page.wait_for_selector("#cmpGrid .cmp-row")
        base = self.page.locator(".cmp-col", has=self.page.locator(".pill.accent", has_text="Base"))
        self.assertEqual(base.locator(".cmp-col-title span").first.inner_text(), "Cohere Transcribe Arabic",
                         "the main transcript is the base, though it is the second column")
        self.assertEqual(base.locator(".main-mark").inner_text(), "MAIN")

        # opening the recording from anywhere opens its main transcript, also after a reload
        self.open(f"#/job/{SOLO}")
        self.page.wait_for_selector("#transcript .line .text")
        self.page.click(f"{sidebar} a.t")
        self.page.wait_for_url(f"**/#/job/{A2}")
        self.page.wait_for_selector("#mainPill", state="visible")

        self.page.click("[data-menu='branchMenu']")
        self.assertEqual(self.page.inner_text("#mainBtn"), "Unmark as the main transcript")
        self.page.click("#mainBtn")
        self.page.wait_for_selector(".toast >> text=This is no longer the recording's main transcript")
        self.page.wait_for_selector("#mainPill", state="hidden")
        self.page.wait_for_selector(f"{sidebar} a.t[href='#/job/{B2}']")
        self.assertEqual(self.labels("#groupBar .pill.ver"), ["Cohere Transcribe Arabic", "ElevenLabs Scribe v2"])

    def test_a_lecture_opens_its_main_transcript(self):
        link = "#jobList details.lectures[data-course='PLbranches01'] a.lec"
        self.open()
        self.page.wait_for_selector(link, state="attached")  # (in the course's folded list)
        self.assertEqual(self.page.get_attribute(link, "href"), f"#/job/{LECTURE_AGAIN}", "without a main one: the newest")
        self.api("POST", f"/api/jobs/{LECTURE}/branch", {"version": 0, "name": "notes"})  # newer, but a branch
        self.page.reload()
        self.page.wait_for_selector(link, state="attached")  # (in the course's folded list)
        self.assertEqual(self.page.get_attribute(link, "href"), f"#/job/{LECTURE_AGAIN}", "not a branch that isn't main")
        self.assertEqual(self.api("POST", f"/api/jobs/{LECTURE}/main", {"main": True})["status"], 200)
        self.page.reload()
        self.page.wait_for_selector(link, state="attached")  # (in the course's folded list)
        self.assertEqual(self.page.get_attribute(link, "href"), f"#/job/{LECTURE}", "the main transcript")
        self.page.click("#jobList details.lectures[data-course='PLbranches01'] summary")
        self.page.click(link)
        self.page.wait_for_url(f"**/#/job/{LECTURE}")
        self.page.wait_for_selector("#mainPill", state="visible")

    def test_a_version_saved_by_an_assistant_shows_in_history(self):
        Config(home=self.home).save_settings(mcp_write=True)  # (off until the user turns it on)
        server = sedjem_mcp.Server(Config(home=self.home))
        meta = {sedjem_mcp.VERSION_KEY: "2026-07-28", sedjem_mcp.CAPABILITIES_KEY: {},
                sedjem_mcp.CLIENT_KEY: {"name": "Claude Desktop", "version": "1.2"}}
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "save_version", "_meta": meta, "arguments": {
                "id": SOLO, "base_version": 0, "message": "A word the model missed",
                "edits": [{"line": 3, "old": LINES[2]["text"], "new": "We need to finish the whole task today, يعني"}]}}})
        self.assertFalse(answer["result"].get("isError"), answer)
        self.job_page(SOLO)
        self.assertEqual(self.page.inner_text("#versionPill"), "v1")
        self.assertEqual(self.texts("#transcript .line .text")[2], "We need to finish the whole task today, يعني")
        self.page.click("#historyBtn")
        top = self.page.wait_for_selector("#history .ver[data-n='1']")
        self.assertEqual(self.texts("#history .ver[data-n='1'] .ver-head .pill"), ["Edit", "Assistant", "Current"])
        self.assertEqual(top.eval_on_selector(".ver-by", "e => e.innerText.trim()"), "Saved by an assistant: Claude Desktop 1.2")
        self.assertEqual(top.eval_on_selector(".ver-msg", "e => e.innerText"), "A word the model missed")
        self.assertEqual(self.page.eval_on_selector_all("#history .ver[data-n='0'] .ver-by", "els => els.length"), 0)


if __name__ == "__main__":
    unittest.main()
