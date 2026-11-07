"""Browser tests of the transcripts of one recording (app/compare.py, app/static/compare.js): the recording
shown once in the sidebar with its transcripts, the switcher on the job page, the compare view (rows lined
up by time, the words that differ, only the rows that differ, a third transcript), and combining: rows and
a time range picked from other models, reviewed as a diff and saved as a new version of the base, which
shows in History and survives a reload. Headless Chromium against a Sedjem server with demo transcripts
(see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import unittest

from ui_support import LINES, UiTestCase

# One recording run with three models (re-runs of the first), a second recording added twice (the same
# audio fingerprint), a recording on its own, and a recording for the combining test.
A, B, C = "20260925-220000-a1a1", "20260925-220100-b2b2", "20260925-220200-c3c3"
TWICE1, TWICE2, ALONE = "20260925-200000-e5e5", "20260925-200100-f6f6", "20260925-190000-a7a7"
BASE, OTHER = "20260925-180000-b8b8", "20260925-180100-c9c9"

# The second model: a little later, its speakers numbered the other way round, and two lines worded differently
SECOND = [dict(x, start=x["start"] + 0.1, end=x["end"] + 0.1, speaker={"1": "2", "2": "1"}[x["speaker"]]) for x in LINES]
SECOND[1]["text"] = "the project ده محتاج تركيز علشان الديدلاين قربت"
SECOND[3]["text"] = "الـ API معتمد على الباك اند اللي الـteam بيعمله"
THIRD = [dict(x) for x in LINES]
THIRD[2]["text"] = "We need to finish the task today"
HOSTED = {"model": "elevenlabs", "model_title": "ElevenLabs Scribe v2", "kind": "hosted"}


class Compare(UiTestCase):
    jobs = ({"jid": A, "title": "Sprint review", "speaker_names": {"1": "Mona", "2": "Omar"}},
            {"jid": B, "title": "Sprint review", "lines": SECOND, "rerun_of": A, **HOSTED,
             "created": "2026-09-25T22:01:00+03:00"},
            {"jid": C, "title": "Sprint review", "lines": THIRD, "rerun_of": B, "model": "whisper-medium",
             "model_title": "whisper-medium code-switching", "created": "2026-09-25T22:02:00+03:00"},
            {"jid": TWICE1, "title": "Design sync", "fingerprint": "demo-same-audio"},
            {"jid": TWICE2, "title": "Design sync again", "fingerprint": "demo-same-audio", **HOSTED},
            {"jid": ALONE, "title": "Budget call"},
            {"jid": BASE, "title": "Retro", "speaker_names": {"1": "Mona", "2": "Omar"}},
            {"jid": OTHER, "title": "Retro", "lines": SECOND, "rerun_of": BASE, **HOSTED})

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.trim())")

    def test_a_recording_shows_once_with_its_transcripts(self):
        self.open()
        self.page.wait_for_selector("#jobList .job-item")
        items = self.page.locator("#jobList .job-item.grouped")
        self.assertEqual(items.count(), 3, "Sprint review, Design sync and Retro")
        sprint = self.page.locator("#jobList .job-item.grouped", has_text="Sprint review")
        self.assertEqual(self.texts("#jobList .job-item.grouped:has-text('Sprint review') .pill.ver"),
                         ["Cohere Transcribe Arabic", "ElevenLabs Scribe v2", "whisper-medium code-switching"],
                         "a re-run of a re-run too, oldest first")
        self.assertEqual(self.texts("#jobList .job-item.grouped:has-text('Design sync') .pill.ver"),
                         ["Cohere Transcribe Arabic", "ElevenLabs Scribe v2"], "the same audio added twice")
        self.assertEqual(self.page.locator("#jobList a.job-item:not(.grouped)", has_text="Budget call").count(), 1)

        sprint.locator(".pill.ver", has_text="ElevenLabs").click()  # a pill opens that transcript
        self.page.wait_for_url(f"**/#/job/{B}")
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.texts("#groupBar .pill.ver.on"), ["ElevenLabs Scribe v2"])
        self.assertEqual(self.page.get_attribute("#groupBar .pill.ver.on", "aria-current"), "page")
        self.assertIn("active", sprint.get_attribute("class"))
        self.page.click("#groupBar .pill.ver >> text=whisper-medium")
        self.page.wait_for_url(f"**/#/job/{C}")
        self.page.wait_for_selector(f"#compareBtn[href='#/compare/{C},{B}']")

        self.open(f"#/job/{ALONE}")
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.page.inner_html("#groupBar"), "", "no switcher for a recording with one transcript")

    def test_the_compare_view_lines_the_transcripts_up_and_marks_the_differences(self):
        self.open(f"#/job/{A}")
        self.page.click("#compareBtn")
        self.page.wait_for_url(f"**/#/compare/{A},{C}")
        self.page.wait_for_selector("#cmpGrid .cmp-row")
        self.assertEqual(self.page.locator("#cmpGrid .cmp-row").count(), 6)
        self.assertEqual(self.page.inner_text(".cmp-tools .count"), "1 of 6 rows differ")
        self.assertEqual(self.texts("#cmpGrid .cmp-row[data-r='2'] mark.d2"), ["whole", "يعني"], "words only Cohere has")
        # Switch the second column to the hosted model, then add the third
        self.page.goto(f"{self.base}/#/compare/{A},{B}")
        self.page.wait_for_selector("#cmpGrid .cmp-col-title >> text=ElevenLabs Scribe v2")
        self.assertEqual(self.page.inner_text(".cmp-tools .count"), "2 of 6 rows differ")
        marked = self.texts("#cmpGrid .cmp-row[data-r='1'] mark.d2")
        self.assertEqual(marked, ["عشان ال deadline", "علشان الديدلاين"], "each side's words that the other lacks")
        self.assertEqual(self.texts("#cmpGrid .cmp-row[data-r='0'] .who"), ["Mona", "Mona"],
                         "ElevenLabs' speaker 2 is shown as the base's Mona")
        self.assertEqual(self.page.input_value("select[data-map$=':2']"), "1")

        self.page.check("#cmpOnlyDiff")
        self.assertEqual(self.page.locator("#cmpGrid .cmp-row:visible").count(), 2)
        self.page.uncheck("#cmpOnlyDiff")

        self.page.click("[data-menu='cmpAdd']")
        self.page.click("#cmpAdd a >> text=whisper-medium")
        self.page.wait_for_url(f"**/#/compare/{A},{B},{C}")
        self.page.wait_for_function("() => document.querySelectorAll('#cmpGrid .cmp-col').length === 3")
        self.assertEqual(self.page.inner_text(".cmp-tools .count"), "3 of 6 rows differ")
        self.assertEqual(self.texts("#cmpGrid .cmp-row[data-r='2'] mark.d2"), [])
        self.assertEqual(self.texts("#cmpGrid .cmp-row[data-r='2'] mark.d1"), ["whole", "يعني"] * 2,
                         "Cohere and ElevenLabs agree and whisper differs: marked lightly")

        # Use as base: the other columns' speakers are matched to it now
        self.page.click(f"[data-base='{B}']")
        self.page.wait_for_selector(f".cmp-col:has([data-drop='{B}']) .pill.accent >> text=Base")
        self.page.click(f"[data-drop='{C}']")
        self.page.wait_for_url(f"**/#/compare/{A},{B}")

    def test_combining_saves_a_new_version_that_history_keeps(self):
        self.open(f"#/compare/{BASE},{OTHER}")
        self.page.wait_for_selector("#cmpGrid .cmp-row")
        self.assertTrue(self.page.is_disabled("#cmpSave"), "nothing picked yet")
        self.page.click("#cmpGrid [data-keep='1:1']")  # row 2 from ElevenLabs
        self.assertEqual(self.page.get_attribute("#cmpGrid [data-keep='1:1']", "aria-pressed"), "true")
        self.assertEqual(self.page.get_attribute("#cmpGrid [data-keep='1:0']", "aria-pressed"), "false")
        self.page.select_option("#cmpRangeFrom", OTHER)
        self.page.fill("#cmpRangeStart", "0:11")
        self.page.fill("#cmpRangeEnd", "0:15")
        self.page.click("#cmpRange button[type='submit']")
        self.assertEqual(self.page.get_attribute("#cmpGrid [data-keep='3:1']", "aria-pressed"), "true", "the time range")
        self.assertEqual(self.page.inner_text("#cmpSummary"), "Cohere Transcribe Arabic, with ElevenLabs Scribe v2 in 2 places")
        self.page.click("#cmpUndo")
        self.assertEqual(self.page.inner_text("#cmpSummary"), "Cohere Transcribe Arabic, with ElevenLabs Scribe v2 in 1 place")
        self.page.click("#cmpRange button[type='submit']")

        self.page.click("#cmpSave")  # reviewed first, like an edit
        self.page.wait_for_selector("#cmpReview[open] .diff")
        self.assertIn("saved as version 2 of the Cohere Transcribe Arabic transcript", self.page.inner_text("#cmpReviewBody"))
        self.assertEqual(self.texts("#cmpReview .cmp-sum-list li"), ["ElevenLabs Scribe v2: 0:03–0:07, 0:11–0:15"])
        self.assertEqual(self.page.inner_text("#cmpReview .diff-stat"), "2 lines changed")
        self.assertEqual(self.texts("#cmpReview .drow.d-add ins")[:1], ["علشان الديدلاين"])
        self.assertEqual(self.api("GET", f"/api/jobs/{BASE}")["json"]["lines"], LINES, "nothing saved yet")
        self.page.fill("#cmpReviewMsg", "Best of both")
        self.page.press("#cmpReviewMsg", "Enter")

        self.page.wait_for_url(f"**/#/job/{BASE}")
        self.page.wait_for_selector(".toast >> text=Saved as version 2")
        self.page.wait_for_selector("#versionPill >> text=v2")
        texts = self.texts("#transcript .line .text")
        self.assertEqual(texts[1], SECOND[1]["text"])
        self.assertEqual(texts[3], SECOND[3]["text"])
        self.assertEqual(texts[0], LINES[0]["text"])
        self.assertEqual(self.api("GET", f"/api/jobs/{OTHER}")["json"]["lines"], SECOND, "the other transcript is left alone")

        def check_history():
            self.page.click("#historyBtn")
            self.page.wait_for_selector("#history[open] .ver")
            top = self.page.locator("#history .ver").first
            self.assertEqual(top.locator(".ver-n").inner_text(), "v2")
            self.assertIn("Combined", top.locator(".ver-head").inner_text())
            self.assertEqual(top.locator(".ver-msg").inner_text(), "Best of both")
            self.assertTrue(top.locator(".ver-sum").inner_text().startswith(
                "With the text of ElevenLabs Scribe v2 (version 0) for 00:03–00:07, 00:11–00:15: 2 lines changed"))
            self.assertEqual(self.texts("#history .ver .ver-n")[-1], "v0", "the model's output is still there")

        check_history()
        self.page.reload()  # saved on the server, not only in the page
        self.page.wait_for_selector("#transcript .line .text")
        self.page.wait_for_selector("#versionPill >> text=v2")
        self.assertEqual(self.texts("#transcript .line .text")[1], SECOND[1]["text"])
        check_history()

        # and it can be undone like any version: restore the model's output
        self.page.on("dialog", lambda d: d.accept())
        self.page.click("#history .ver[data-n='0'] [data-ver-restore='0']")
        self.page.wait_for_selector("#versionPill >> text=v3")
        self.assertEqual(self.api("GET", f"/api/jobs/{BASE}")["json"]["lines"], LINES)

    def test_leaving_with_picks_asks_first(self):
        self.open(f"#/compare/{A},{B}")
        self.page.wait_for_selector("#cmpGrid .cmp-row")
        self.page.click("#cmpGrid [data-keep='1:1']")
        asked = []
        self.page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
        with self.page.expect_event("dialog"):
            self.page.click("#jobList a.job-item >> text=Budget call")
        self.assertEqual(asked, ["Leave without saving the text you picked?"])
        self.page.wait_for_url(f"**/#/compare/{A},{B}")
        self.assertEqual(self.page.get_attribute("#cmpGrid [data-keep='1:1']", "aria-pressed"), "true", "the picks stay")


if __name__ == "__main__":
    unittest.main()
