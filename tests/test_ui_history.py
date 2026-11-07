"""Browser tests of the version history: edits made in one session, reviewed as a diff and saved as one
version (with a message, or with none), quick changes outside edit mode, the History dialog (the list,
the changes in a version, comparing two versions, messages, going back to the original) and exports of a
version. Headless Chromium against a Sedjem server with demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import unittest

from ui_support import LINES, UiTestCase

SESSION, EMPTY, QUICK, BACK, EXPORT, DISCARD = ("20260925-231000-c3d4", "20260925-231100-d4e5", "20260925-231200-e5f6",
                                                "20260925-231300-f6a7", "20260925-231400-a7b8", "20260925-231500-b8c9")
# stands in for the browser's Save dialog (showSaveFilePicker) and keeps what is written
SAVE_PICKER = """
    window.showSaveFilePicker = async (opts) => ({ name: opts.suggestedName, createWritable: async () => {
        const parts = [];
        return { write: async (b) => parts.push(b),
                 close: async () => { window.__saved = { name: opts.suggestedName, text: await new Blob(parts).text() }; } };
    } });"""


def changed(i, text):
    return [{**x, "text": text} if n == i else x for n, x in enumerate(LINES)]


class History(UiTestCase):
    jobs = ({"jid": SESSION, "title": "Edit session"}, {"jid": EMPTY, "title": "Empty message"},
            {"jid": QUICK, "title": "Quick changes"}, {"jid": BACK, "title": "Going back"},
            {"jid": EXPORT, "title": "Exports"}, {"jid": DISCARD, "title": "Discard"})

    def job_page(self, jid):
        self.open(f"#/job/{jid}")
        self.page.wait_for_selector("#transcript .line .text")

    def edit_line(self, i, text):
        self.page.locator(f"#transcript .line[data-i='{i}'] .text").click()
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.type(text)

    def versions(self, jid):
        return self.api("GET", f"/api/jobs/{jid}/versions")["json"]["versions"]

    def texts(self):
        return self.page.eval_on_selector_all("#transcript .line .text", "els => els.map(e => e.innerText)")

    def test_an_edit_session_is_reviewed_as_a_diff_and_saved_as_one_version(self):
        self.job_page(SESSION)
        self.assertEqual(self.page.inner_text("#versionPill"), "v0", "the model's output is version 0")
        self.page.click("#editBtn")
        self.edit_line(0, "order ال promo code عشان أعمل checkout بسرعة")
        self.edit_line(4, "Let's start the sprint review now.")
        self.page.select_option("#transcript select[data-line='5']", "1")
        self.page.fill("input[data-name='2']", "Sara")
        self.page.press("input[data-name='2']", "Enter")  # in edit mode a rename waits for the review too
        self.page.wait_for_selector(".editbar >> text=Unsaved changes")
        self.assertEqual(len(self.versions(SESSION)), 1)
        self.assertEqual(self.api("GET", f"/api/jobs/{SESSION}")["json"]["job"]["speaker_names"], {})

        self.page.click("#editBtn")  # Done editing: the review comes first
        review = self.page.wait_for_selector("#review[open] .diff")
        self.assertEqual(self.page.inner_text("#review .diff-base"), "Compared with version 0, the current one")
        self.assertEqual(self.page.inner_text("#review .diff-stat"), "3 lines changed, 1 speaker renamed")
        self.assertEqual(self.page.inner_text("#review .diff-facts"), "Speaker 2 → Sara")
        removed = self.page.eval_on_selector_all("#review .drow.d-del del", "els => els.map(e => e.innerText)")
        added = self.page.eval_on_selector_all("#review .drow.d-add ins", "els => els.map(e => e.innerText)")
        self.assertEqual((removed[:2], added[:2]), (["discount", "planning"], ["promo", "review"]))
        self.assertEqual(self.page.get_attribute("#review .drow.d-add .text", "dir"), "rtl", "an Arabic line")
        self.assertEqual(self.page.eval_on_selector_all("#review .drow.d-add .who ins", "els => els.map(e => e.innerText)"),
                         ["Speaker 1"], "the line given to another speaker")
        self.assertEqual(self.page.inner_text("#review .dsep"), "1 unchanged line")
        self.page.uncheck("#review [data-diff-only]")
        self.assertEqual(review.eval_on_selector_all(".drow.d-same", "els => els.length"), 3)

        self.page.fill("#reviewMsg", "Checked against the recording")
        self.page.press("#reviewMsg", "Enter")  # Enter saves
        self.page.wait_for_selector(".toast >> text=Saved as version 1")
        self.assertEqual(self.page.inner_text("#versionPill"), "v1", "the first saved edit is version 1")
        self.assertFalse(self.page.is_visible(".editbar"))
        v = self.versions(SESSION)[-1]
        self.assertEqual((v["n"], v["kind"], v["message"], v["summary"]),
                         (1, "edit", "Checked against the recording", "3 lines changed, Speaker 2 renamed to Sara"))

        self.page.reload()
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.page.inner_text("#versionPill"), "v1")
        self.assertEqual(self.texts()[0], "order ال promo code عشان أعمل checkout بسرعة")
        self.assertEqual(self.texts()[4], "Let's start the sprint review now.")
        self.assertEqual(self.page.input_value("input[data-name='2']"), "Sara")

    def test_back_to_editing_keeps_the_edits_and_an_empty_message_is_fine(self):
        self.job_page(EMPTY)
        self.page.click("#editBtn")
        self.edit_line(2, "We need to finish the whole task tomorrow يعني")
        self.page.click("#saveBtn")
        self.page.wait_for_selector("#review[open] .diff")
        self.page.click("#reviewBack")
        self.page.wait_for_selector("#review", state="hidden")
        self.assertEqual(self.texts()[2], "We need to finish the whole task tomorrow يعني", "the edit is still there")
        self.page.click("#saveBtn")
        self.page.wait_for_selector("#review[open] .diff")
        self.assertEqual(self.page.input_value("#reviewMsg"), "")
        self.page.click("#reviewForm button[type='submit']")
        self.page.wait_for_selector(".toast >> text=Saved as version 1")
        self.assertEqual([(v["n"], v["message"]) for v in self.versions(EMPTY)], [(0, ""), (1, "")])

        self.page.click("#editBtn")  # a session that ends where it began saves nothing
        self.edit_line(2, "We need to finish the whole task tomorrow يعني")
        self.page.click("#editBtn")
        self.page.wait_for_selector(".toast >> text=Nothing changed, so no new version")
        self.assertEqual(len(self.versions(EMPTY)), 2)

    def test_in_edit_mode_the_title_waits_for_the_review_and_discard_drops_the_edits(self):
        self.job_page(DISCARD)
        self.page.click("#editBtn")
        self.page.click("#jobTitle")
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.type("A new title")
        self.page.keyboard.press("Enter")
        self.page.wait_for_selector(".editbar >> text=Unsaved changes")
        self.assertEqual(self.api("GET", f"/api/jobs/{DISCARD}")["json"]["job"]["title"], "Discard")
        self.edit_line(1, "the project ده محتاج وقت")
        self.page.click("#saveBtn")
        self.page.wait_for_selector("#review[open] .diff-facts ins >> text=A new title")
        self.assertEqual(self.page.inner_text("#review .diff-stat"), "1 line changed, title changed")
        self.page.click("#reviewBack")
        self.page.once("dialog", lambda d: d.accept())
        self.page.click("#discardBtn")
        self.page.wait_for_selector(".editbar", state="detached")
        self.assertEqual((self.page.inner_text("#jobTitle"), self.texts()[1]), ("Discard", LINES[1]["text"]))
        self.assertEqual(len(self.versions(DISCARD)), 1)

    def test_quick_changes_close_together_are_one_version(self):
        self.job_page(QUICK)
        self.page.fill("input[data-name='1']", "Mona")
        self.page.press("input[data-name='1']", "Enter")
        self.page.wait_for_selector("#versionPill >> text=v1")
        self.page.click("[data-menu='merge-2']")
        self.page.once("dialog", lambda d: d.accept())
        self.page.click("[data-merge-from='2'][data-merge-into='1']")
        self.page.wait_for_selector(".toast >> text=Speakers merged")
        self.page.click("#jobTitle")
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.type("Planning, week 40")
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("() => S.job.title === 'Planning, week 40'")
        self.assertEqual(self.page.inner_text("#versionPill"), "v1")
        v = self.versions(QUICK)[-1]
        self.assertEqual((v["n"], v["kind"], v["summary"]),
                         (1, "merge", "Speaker 2 merged into Mona (3 lines), Speaker 1 renamed to Mona, "
                                      "title changed to “Planning, week 40”"))

        self.page.click("#historyBtn")  # a message can be added afterwards
        self.page.click("#history [data-ver-msg='1']")
        self.page.fill("#history [data-ver-form='1'] input", "Names from the meeting invite")
        self.page.press("#history [data-ver-form='1'] input", "Enter")
        self.page.wait_for_selector("#history .ver[data-n='1'] .ver-msg >> text=Names from the meeting invite")
        self.assertEqual(self.versions(QUICK)[-1]["message"], "Names from the meeting invite")

    def test_history_shows_the_changes_compares_versions_and_restores_the_original(self):
        base = f"/api/jobs/{BACK}"
        self.open()
        self.assertEqual(self.api("PATCH", base, {"lines": changed(0, "order ال discount code بسرعة"),
                                                  "message": "First pass"})["status"], 200)
        second = changed(0, "order ال discount code بسرعة")
        second[4] = {**second[4], "text": "Let's start the sprint review now."}
        self.assertEqual(self.api("PATCH", base, {"lines": second, "message": ""})["json"]["version"], 2)
        self.job_page(BACK)
        self.page.click("#versionPill")  # the "v2" next to the title opens the history too
        self.page.wait_for_selector("#history[open] .ver")
        rows = self.page.eval_on_selector_all("#history .ver", """els => els.map(e => [e.querySelector('.ver-n').innerText,
            [...e.querySelectorAll('.pill')].map(p => p.innerText).join(' '), e.querySelector('.ver-msg')?.innerText || ''])""")
        self.assertEqual(rows, [["v2", "Edit Current", ""], ["v1", "Edit", "First pass"], ["v0", "Original", ""]])
        self.assertFalse(self.page.is_visible("#history [data-ver-restore='2']"), "no restoring the current one")

        self.page.click("#history [data-ver-view='2']")
        self.page.wait_for_selector("#history .diff")
        self.assertEqual(self.page.inner_text("#history .diff-stat"), "1 line changed")
        self.assertEqual(self.page.inner_text("#history .drow.d-del del"), "planning")
        self.page.select_option("#verAgainst", "0")  # compare with any other version, the original too
        self.page.wait_for_selector("#history .diff-stat >> text=2 lines changed")
        self.page.click("#verBack")
        self.page.once("dialog", lambda d: d.accept())
        self.page.click("#history [data-ver-restore='0']")
        self.page.wait_for_selector(".toast >> text=Version 0 restored as version 3")
        self.assertEqual(self.page.inner_text("#versionPill"), "v3")
        self.assertEqual(self.texts(), [x["text"] for x in LINES])

        self.page.reload()
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual((self.page.inner_text("#versionPill"), self.texts()), ("v3", [x["text"] for x in LINES]))
        v = self.versions(BACK)[-1]
        self.assertEqual((v["n"], v["kind"], v["restored_from"]), (3, "restore", 0))

    def test_exports_and_copy_of_a_version(self):
        self.page.add_init_script(SAVE_PICKER)
        self.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.base)
        self.open()
        self.api("PATCH", f"/api/jobs/{EXPORT}", {"lines": changed(1, "the project ده محتاج وقت"), "message": "Shorter"})
        self.job_page(EXPORT)
        self.page.click("#historyBtn")
        self.page.click("#history [data-menu='verExport0']")
        self.page.click("#verExport0 a[data-fmt='txt']")
        saved = self.page.wait_for_function("() => window.__saved").json_value()
        self.assertEqual(saved["name"], "Exports-v0.txt")
        self.assertIn(LINES[1]["text"], saved["text"], "version 0 as the model wrote it")
        self.assertNotIn("محتاج وقت", saved["text"])

        self.page.click("#history [data-ver-view='0']")
        self.assertEqual(self.page.inner_text("#history .ver-title"), "Version 0")
        self.assertEqual(self.page.inner_text("#history .ver-view-head + .ver-head .pill"), "Original")
        self.page.click("#verCopy")
        self.page.wait_for_selector(".toast >> text=Version 0 copied")
        copied = self.page.evaluate("() => navigator.clipboard.readText()")
        self.assertIn(LINES[1]["text"], copied)
        self.assertNotIn("Recording:", copied, "the text only, as Copy text")

        self.page.keyboard.press("Escape")  # and the page's own Export is the current version
        self.page.wait_for_selector("#history", state="hidden")
        self.page.evaluate("() => { window.__saved = null; }")
        self.page.click("[data-menu='exportMenu']")
        self.page.click("#exportMenu a[data-fmt='txt']")
        saved = self.page.wait_for_function("() => window.__saved").json_value()
        self.assertEqual(saved["name"], "Exports.txt")
        self.assertIn("the project ده محتاج وقت", saved["text"])


if __name__ == "__main__":
    unittest.main()
