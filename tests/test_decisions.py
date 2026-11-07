"""Tests for the decision records: the architecture decision records in docs/adr and the agent decision records
(AgDRs) in docs/agdr (docs/adr/0001-record-decisions-as-adrs-and-agdrs.md). For both folders: the file names, the
numbers without gaps, the frontmatter and its statuses, each record in its README's index with the same title and
status, and superseded records that point at real ones. For an AgDR also what the AgDR specification asks of its
body: the Y-statement under the title, at least two options and a decision that starts "Chosen:". Both folders read
as plain prose, without bold or em dashes. Offline, standard library only.
Run: .venv/bin/python -m unittest tests.test_decisions -v
"""
import re
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADR = ROOT / "docs" / "adr"
AGDR = ROOT / "docs" / "agdr"

ADR_NAME = re.compile(r"(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
AGDR_NAME = re.compile(r"AgDR-(\d{4})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md")
ADR_KEYS = {"status", "date", "deciders"}
AGDR_KEYS = {"id", "timestamp", "agent", "model", "trigger", "status"}
ADR_STATUS = re.compile(r"Proposed|Accepted|Deprecated|Superseded by (\d{4})")
AGDR_STATUSES = {"proposed", "executed", "superseded", "deprecated"}
TRIGGERS = {"user-prompt", "hook", "automation", "self-initiated"}
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
Y_STATEMENT = re.compile(r"> In the context of (.+?), facing (.+?), I decided (.+?) to achieve (.+?), accepting (.+)\.")
LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def records(folder):
    """The record files of a folder, its README left out."""
    return sorted(p for p in folder.glob("*.md") if p.name != "README.md")


def parse(path):
    """A record's frontmatter as a dict, and the lines of its body that aren't blank."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, []
    fields = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in fields.items()}, [line for line in text[m.end():].splitlines() if line.strip()]


def section(lines, heading):
    """The lines under a ## heading, up to the next one."""
    if f"## {heading}" not in lines:
        return []
    rest = lines[lines.index(f"## {heading}") + 1:]
    return rest[:next((i for i, line in enumerate(rest) if line.startswith("## ")), len(rest))]


def index(folder):
    """The rows of a README's index: {file name: (first cell, title, status as text)}."""
    rows = {}
    for line in (folder / "README.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        link = re.fullmatch(r"\[([^\]]+)\]\(([^)\s]+)\)", cells[1]) if len(cells) >= 4 else None
        if link:
            rows[link.group(2)] = (cells[0], link.group(1), LINK.sub(r"\1", cells[2]))
    return rows


def status_links(folder):
    """The targets of the links in the status column of a README's index."""
    found = []
    for line in (folder / "README.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4:
            found += [target for _, target in LINK.findall(cells[2])]
    return found


class BothFolders(unittest.TestCase):
    def test_numbered_in_order_without_gaps(self):
        for folder, pattern in ((ADR, ADR_NAME), (AGDR, AGDR_NAME)):
            files = records(folder)
            with self.subTest(folder.name):
                self.assertTrue(files)
                for path in files:
                    self.assertRegex(path.name, f"^{pattern.pattern}$")
                numbers = [int(pattern.fullmatch(p.name).group(1)) for p in files if pattern.fullmatch(p.name)]
                self.assertEqual(sorted(numbers), list(range(1, len(files) + 1)), "unique, from 1, without gaps")

    def test_plain_prose_without_bold_or_em_dashes(self):
        for path in [*sorted(ADR.glob("*.md")), *sorted(AGDR.glob("*.md"))]:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path.relative_to(ROOT).as_posix()):
                self.assertNotIn("**", text, "no bold")
                self.assertNotIn("—", text, "no em dashes")

    def test_every_record_is_in_its_index_and_nothing_else(self):
        for folder in (ADR, AGDR):
            with self.subTest(folder.name):
                self.assertEqual(set(index(folder)), {p.name for p in records(folder)})

    def test_superseded_by_links_reach_a_record(self):
        for folder in (ADR, AGDR):
            names = {p.name for p in records(folder)}
            for target in status_links(folder):
                with self.subTest(folder.name, link=target):
                    self.assertIn(target, names)


class ArchitectureDecisionRecords(unittest.TestCase):
    def setUp(self):
        self.files = records(ADR)
        self.index = index(ADR)
        self.numbers = {int(p.name[:4]) for p in self.files}

    def test_frontmatter(self):
        for path in self.files:
            fields, _ = parse(path)
            with self.subTest(path.name):
                self.assertEqual(set(fields), ADR_KEYS)
                self.assertRegex(fields["status"], f"^(?:{ADR_STATUS.pattern})$")
                self.assertEqual(date.fromisoformat(fields["date"]).isoformat(), fields["date"])
                self.assertTrue(fields["deciders"])

    def test_superseded_names_another_record(self):
        for path in self.files:
            superseded = ADR_STATUS.fullmatch(parse(path)[0]["status"]).group(1)
            if superseded:
                with self.subTest(path.name):
                    self.assertIn(int(superseded), self.numbers)
                    self.assertNotEqual(int(superseded), int(path.name[:4]))

    def test_title_sections_and_index_row(self):
        for path in self.files:
            fields, lines = parse(path)
            number = int(path.name[:4])
            with self.subTest(path.name):
                title = re.fullmatch(rf"# {number}\. (.+)", lines[0])
                self.assertTrue(title, "the first line is '# N. Title'")
                self.assertEqual([line[3:] for line in lines if line.startswith("## ")],
                                 ["Context", "Decision", "Consequences"])
                self.assertIn(path.name, self.index, "a row in README.md's index")
                cell, indexed_title, status = self.index[path.name]
                self.assertEqual(cell, path.name[:4])
                self.assertEqual(indexed_title, title.group(1))
                self.assertEqual(status, fields["status"])


class AgentDecisionRecords(unittest.TestCase):
    def setUp(self):
        self.files = records(AGDR)
        self.index = index(AGDR)
        self.ids = {parse(p)[0].get("id"): p for p in self.files}

    def test_frontmatter(self):
        for path in self.files:
            fields, _ = parse(path)
            with self.subTest(path.name):
                self.assertLessEqual(AGDR_KEYS, set(fields))
                self.assertLessEqual(set(fields), AGDR_KEYS | {"session", "supersedes"})
                self.assertEqual(fields["id"], path.name[:9])
                self.assertLessEqual(len(AGDR_NAME.fullmatch(path.name).group(2)), 50, "a slug of at most 50 characters")
                self.assertRegex(fields["timestamp"], f"^{TIMESTAMP.pattern}$", "ISO 8601 with a time and its zone")
                self.assertIsNotNone(datetime.fromisoformat(fields["timestamp"]).tzinfo)
                self.assertIn(fields["trigger"], TRIGGERS)
                self.assertIn(fields["status"], AGDR_STATUSES)
                for key in ("agent", "model"):
                    self.assertRegex(fields[key], r"^[\w.-]+$", "a name, not a placeholder")

    def test_supersedes_names_a_superseded_record(self):
        for path in self.files:
            fields, _ = parse(path)
            if "supersedes" in fields:
                with self.subTest(path.name):
                    self.assertIn(fields["supersedes"], self.ids)
                    self.assertNotEqual(fields["supersedes"], fields["id"])
                    self.assertEqual(parse(self.ids[fields["supersedes"]])[0]["status"], "superseded")

    def test_title_and_y_statement(self):
        for path in self.files:
            _, lines = parse(path)
            with self.subTest(path.name):
                self.assertRegex(lines[0], r"^# \S")
                statement = Y_STATEMENT.fullmatch(lines[1])
                self.assertTrue(statement, "the Y-statement is the first line after the title, with all five parts")
                for part in statement.groups():
                    self.assertGreater(len(part.split()), 2, part)

    def test_options_and_decision(self):
        for path in self.files:
            _, lines = parse(path)
            with self.subTest(path.name):
                table = [line for line in section(lines, "Options Considered") if line.startswith("|")]
                self.assertRegex(table[1], r"^\|[-|: ]+\|$", "a table with a header")
                rows = [[c.strip() for c in row.strip("|").split("|")] for row in table[2:]]
                self.assertGreaterEqual(len(rows), 2, "at least two options")
                for row in rows:
                    self.assertEqual(len(row), 3, row)
                    self.assertTrue(all(row), "an option with its pros and cons")
                decision = section(lines, "Decision")
                self.assertTrue(decision[0].startswith("Chosen: "))
                self.assertIn(" because ", " ".join(decision))

    def test_index_row(self):
        for path in self.files:
            fields, lines = parse(path)
            with self.subTest(path.name):
                self.assertIn(path.name, self.index, "a row in README.md's index")
                cell, title, status = self.index[path.name]
                self.assertEqual(cell, fields["id"])
                self.assertEqual(title, lines[0][2:])
                if fields["status"] == "superseded":
                    later = [i for i, p in self.ids.items() if parse(p)[0].get("supersedes") == fields["id"]]
                    self.assertEqual(status, f"superseded by {later[0]}" if later else "superseded")
                else:
                    self.assertEqual(status, fields["status"])

    def test_y_statement_pattern(self):
        good = ("> In the context of a React Native app, facing slow list rendering with 1000+ items, I decided to use "
                "FlashList to achieve 60fps scrolling, accepting the learning curve of a new API.")
        self.assertTrue(Y_STATEMENT.fullmatch(good))
        self.assertFalse(Y_STATEMENT.fullmatch(good.replace(", accepting the learning curve of a new API", "")))
        self.assertFalse(Y_STATEMENT.fullmatch(good.replace("> ", "")), "a blockquote")


if __name__ == "__main__":
    unittest.main()
