"""Browser tests of the order of the sidebar (app/static/app.js): each order offered next to the search, with the
headings that follow it (today and the months for the date orders, none for the others); a recording with several
transcripts ordered as one item by its main transcript, and a course as one item whose lectures stay in lecture
order; the search on top of an order; the choice remembered in the browser, and newest first when what was kept is
unknown; and the choice by keyboard, by its name, and in a narrow window. Headless Chromium against a Sedjem server
with demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_sidebar_order.py" -v
"""
import unittest
from datetime import datetime, timedelta

from ui_support import LINES, UiTestCase

NOW = datetime.now().astimezone().replace(microsecond=0)
TODAY = max(NOW - timedelta(minutes=1), NOW.replace(hour=0, minute=0, second=0)).isoformat()  # a minute ago, still today

# Planning: run twice, the first run its main transcript (the re-run was renamed); a course of two lectures (lecture 2
# added last, its title first in the alphabet); recordings on their own: one added today, one whose length was never
# read, one from last year, one with an Arabic title. Each order puts them differently.
A1, A2 = "20260926-100000-a1a1", "20260926-100100-a2a2"
STANDUP, TEAM, BROKEN, BUDGET = "20260926-100030-b1b1", "20260901-120000-c1c1", "20260715-080000-e1e1", "20250310-150000-f1f1"
L1, L2 = "20260801-090000-d1d1", "20260801-090500-d2d2"
DESIGN = NOW.strftime("%Y%m%d-%H%M%S-") + "a0a0"
COURSE = {"id": "PLsorting", "title": "Python course", "url": "https://www.youtube.com/playlist?list=PLsorting",
          "count": 2, "folder": "Python course"}
LECTURE = lambda n: {"course": {**COURSE, "index": n}, "youtube_id": f"vidsorting{n}",
                     "source_url": f"https://www.youtube.com/watch?v=vidsorting{n}"}
HOSTED = {"model": "elevenlabs", "model_title": "ElevenLabs Scribe v2", "kind": "hosted"}


def at(created, finished=None, recorded=None, audio_s=None):
    """A job's times: added, finished five seconds later; recorded, when the recording's date is known; its length."""
    fields = {"created": created, "finished": finished or (datetime.fromisoformat(created) + timedelta(seconds=5)).isoformat()}
    if recorded:
        fields["source"] = {"name": "call.m4a", "extension": "m4a", "recorded": recorded}
    return {**fields, "audio_s": audio_s}


def heading(iso):
    """The heading the list puts over a date: Today, else its month, with the year when it isn't this year."""
    d = datetime.fromisoformat(iso).astimezone()
    if d.date() == datetime.now().astimezone().date():
        return "Today"
    return d.strftime("%B") + ("" if d.year == datetime.now().year else f" {d.year}")


def with_headings(items):
    """[(title, date)] as the list shows them: "# heading" before the first item of each day or month."""
    out, last = [], None
    for title, iso in items:
        if heading(iso) != last:
            last = heading(iso)
            out.append(f"# {last}")
        out.append(title)
    return out


ADDED = [("Design sync", TODAY), ("Standup", "2026-09-26T10:00:30+03:00"),
         ("Planning", "2026-09-26T10:00:00+03:00"),  # its main transcript's, not the re-run's at 10:01
         ("اجتماع الفريق", "2026-09-01T12:00:00+03:00"), ("Python course", "2026-08-01T09:05:00+03:00"),
         ("Broken file", "2026-07-15T08:00:00+03:00"), ("Budget call", "2025-03-10T15:00:00+03:00")]
BY_TITLE = ["Broken file", "Budget call", "Design sync", "Planning", "Python course", "Standup", "اجتماع الفريق"]
LONGEST = ["اجتماع الفريق", "Python course", "Budget call", "Planning", "Design sync", "Standup", "Broken file"]


class SidebarOrder(UiTestCase):
    jobs = ({"jid": A1, "title": "Planning", "main": True, **at("2026-09-26T10:00:00+03:00", recorded="2026-09-25T18:00:00+03:00", audio_s=120)},
            {"jid": A2, "title": "Weekly planning", "rerun_of": A1, **HOSTED,
             **at("2026-09-26T10:01:00+03:00", recorded="2026-09-25T18:00:00+03:00", audio_s=120)},
            {"jid": STANDUP, "title": "Standup", **at("2026-09-26T10:00:30+03:00", audio_s=30)},  # an older job: no date of its own
            {"jid": TEAM, "title": "اجتماع الفريق", **at("2026-09-01T12:00:00+03:00", recorded="2026-08-30T11:00:00+03:00", audio_s=900)},
            {"jid": L1, "title": "Variables", **LECTURE(1), **at("2026-08-01T09:00:00+03:00", audio_s=200)},
            {"jid": L2, "title": "Loops", **LECTURE(2), **at("2026-08-01T09:05:00+03:00", audio_s=500)},
            {"jid": BROKEN, "title": "Broken file", "status": "failed", "error": "the file has no audio track",
             **at("2026-07-15T08:00:00+03:00")},
            {"jid": BUDGET, "title": "Budget call", **at("2025-03-10T15:00:00+03:00", recorded="2025-02-01T10:00:00+02:00", audio_s=600)},
            {"jid": DESIGN, "title": "Design sync", **at(TODAY, TODAY, recorded="2024-05-10T09:00:00+03:00", audio_s=45)})

    def shown(self):
        """The list as shown: each heading as "# heading", each item by its title."""
        self.page.wait_for_selector("#jobList .job-item")
        return self.page.eval_on_selector_all("#jobList > *", """els => els.map(e => e.matches('.job-group')
            ? '# ' + e.textContent.trim() : e.querySelector('.t').textContent.trim())""")

    def sort(self, value):
        self.page.select_option("#jobSort", value)
        return self.shown()

    def lectures(self):
        return self.page.eval_on_selector_all("#jobList .lec .lt", "els => els.map(e => e.textContent.trim())")

    def kept(self):
        return self.page.evaluate("() => localStorage.getItem('sedjem-sort')")

    def test_newest_first_is_the_default_with_today_and_the_months(self):
        self.open()
        self.assertEqual(self.page.input_value("#jobSort"), "newest")
        self.assertEqual(self.page.eval_on_selector_all("#jobSort option", "os => os.map(o => o.textContent)"),
                         ["Newest first", "Oldest first", "Title A to Z", "Title Z to A", "Longest first", "Shortest first",
                          "Last changed", "Recording date"])
        self.assertEqual(self.shown(), with_headings(ADDED))
        self.assertEqual(self.shown()[:2], ["# Today", "Design sync"])
        self.assertEqual(self.page.locator("#jobList .job-item").count(), 7, "a recording once, a course once")
        self.assertEqual(self.lectures(), ["Variables", "Loops"])
        self.assertIsNone(self.kept(), "nothing is kept until an order is chosen")

    def test_oldest_first(self):
        self.open()
        self.assertEqual(self.sort("oldest"), with_headings(ADDED[::-1]))
        self.assertEqual(self.lectures(), ["Variables", "Loops"])

    def test_title_a_to_z_and_z_to_a_without_headings(self):
        self.open()
        self.assertEqual(self.sort("title"), BY_TITLE, "Planning by its main transcript, the course by its own title")
        self.assertEqual(self.lectures(), ["Variables", "Loops"], "lectures in lecture order, not by title")
        self.assertEqual(self.sort("title-desc"), BY_TITLE[::-1])
        self.assertEqual(self.lectures(), ["Variables", "Loops"])

    def test_longest_and_shortest_first(self):
        self.open()
        # the course by its lectures together (700 s), and the recording never read last in both
        self.assertEqual(self.sort("longest"), LONGEST)
        self.assertEqual(self.sort("shortest"), LONGEST[-2::-1] + ["Broken file"])
        self.assertEqual(self.lectures(), ["Variables", "Loops"])

    def test_last_changed(self):
        self.open()
        fixed = [{**x, "text": "order ال promo code بسرعة"} if i == 0 else x for i, x in enumerate(LINES)]
        self.assertEqual(self.api("PATCH", f"/api/jobs/{A2}", {"lines": fixed})["status"], 200)  # not the main transcript
        self.assertEqual(self.api("PATCH", f"/api/jobs/{BUDGET}", {"lines": fixed})["status"], 200)
        self.page.evaluate("refreshJobs()")
        changed = [("Budget call", NOW.isoformat()), ("Design sync", TODAY),
                   ("Standup", "2026-09-26T10:00:35+03:00"), ("Planning", "2026-09-26T10:00:05+03:00"),
                   ("اجتماع الفريق", "2026-09-01T12:00:05+03:00"), ("Python course", "2026-08-01T09:05:05+03:00"),
                   ("Broken file", "2026-07-15T08:00:05+03:00")]
        self.assertEqual(self.sort("changed"), with_headings(changed),
                         "Budget call first, saved just now; Planning by its main transcript, which wasn't changed")

    def test_recording_date(self):
        self.open()
        recorded = [("Standup", "2026-09-26T10:00:30+03:00"),  # an older job: the date it was added
                    ("Planning", "2026-09-25T18:00:00+03:00"), ("اجتماع الفريق", "2026-08-30T11:00:00+03:00"),
                    ("Python course", "2026-08-01T09:05:00+03:00"), ("Broken file", "2026-07-15T08:00:00+03:00"),
                    ("Budget call", "2025-02-01T10:00:00+02:00"), ("Design sync", "2024-05-10T09:00:00+03:00")]
        self.assertEqual(self.sort("recorded"), with_headings(recorded))
        self.assertEqual(self.lectures(), ["Variables", "Loops"])

    def test_search_works_on_top_of_the_order(self):
        self.open()
        self.sort("title-desc")
        self.page.fill("#jobSearch", "p")  # Planning, Python course (and its lecture Loops), Standup
        self.assertEqual(self.shown(), ["Standup", "Python course", "Planning"])
        self.assertEqual(self.sort("oldest"), with_headings([("Python course", "2026-08-01T09:05:00+03:00"),
                                                             ("Planning", "2026-09-26T10:00:00+03:00"),
                                                             ("Standup", "2026-09-26T10:00:30+03:00")]))
        self.page.fill("#jobSearch", "weekly")  # the re-run's title finds its recording, shown by its main transcript
        self.assertEqual(self.shown(), ["# " + heading("2026-09-26T10:00:00+03:00"), "Planning"])
        self.page.fill("#jobSearch", "nothing like this")
        self.assertEqual(self.page.inner_text("#jobList"), "No matches")
        self.page.fill("#jobSearch", "")
        self.assertEqual(self.shown(), with_headings(ADDED[::-1]))

    def test_the_choice_is_remembered_and_an_unknown_one_is_newest_first(self):
        self.open()
        self.sort("longest")
        self.assertEqual(self.kept(), "longest")
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.assertEqual(self.page.input_value("#jobSort"), "longest")
        self.assertEqual(self.shown(), LONGEST)
        self.page.goto(f"{self.base}/#/job/{TEAM}")  # it stays while moving around the app
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.shown(), LONGEST)

        for stored in ("alphabetical", "toString", ""):  # an order this version doesn't know, or none
            with self.subTest(stored=stored):
                self.page.evaluate("(v) => localStorage.setItem('sedjem-sort', v)", stored)
                self.page.reload()
                self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
                self.assertEqual(self.page.input_value("#jobSort"), "newest")
                self.assertEqual(self.shown(), with_headings(ADDED))

    def test_by_keyboard_with_its_name_and_in_a_narrow_window(self):
        self.page.set_viewport_size({"width": 360, "height": 740})
        self.open()
        self.shown()  # the app has opened its first page, which closes the sidebar
        self.page.click("#menuToggle")
        self.page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().left === 0")  # slid in
        sort = self.page.get_by_role("combobox", name="Sort transcriptions")
        self.assertEqual(sort.count(), 1, "a select named for screen readers")
        box, side = sort.bounding_box(), self.page.locator("#sidebar").bounding_box()
        self.assertLessEqual(box["x"] + box["width"], side["x"] + side["width"], "beside the search, inside the sidebar")
        self.assertEqual(round(box["y"]), round(self.page.locator("#jobSearch").bounding_box()["y"]), "on the search's row")
        self.assertTrue(self.page.evaluate("() => { const s = document.getElementById('sidebar'); return s.scrollWidth <= s.clientWidth; }"))

        self.page.focus("#jobSearch")
        self.page.keyboard.press("Tab")  # the search, then the order
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "jobSort")
        self.page.keyboard.press("Enter")  # opens its list
        self.page.keyboard.press("ArrowDown")
        self.page.keyboard.press("ArrowDown")  # Title A to Z
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("() => document.getElementById('jobSort').value === 'title'")
        self.assertEqual(self.shown(), BY_TITLE)
        self.assertEqual(self.kept(), "title")
        self.assertTrue(self.page.evaluate("() => document.getElementById('sidebar').classList.contains('open')"),
                        "choosing an order leaves the sidebar open")


if __name__ == "__main__":
    unittest.main()
