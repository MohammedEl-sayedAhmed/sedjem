"""Tests for the charts in the docs (docs/charts.py). The charts are redrawn and must equal the
committed SVG files and be well-formed XML. Every number drawn, every bar and whisker must match its
source: results/summary.json and results/*-diarization.json for the public sets, docs/charts-data.json
for the real meetings. The public numbers must be the ones in the docs' tables, and charts-data.json
must hold the numbers the docs publish. The generator reads nothing else, so it never touches the
private meeting data.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import contextlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CHART_DIR = ROOT / "docs" / "images" / "charts"
SUMMARY_PATH = ROOT / "results" / "summary.json"
ARZEN_PATH, AMI_PATH = ROOT / "results" / "arzen-diarization.json", ROOT / "results" / "ami-diarization.json"
DATA_PATH = ROOT / "docs" / "charts-data.json"
SVG = "{http://www.w3.org/2000/svg}"
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Names in the charts that contain digits without being results
NAMES = re.compile(r"large-v3|Qwen3-ASR-1\.7B|R2T2|Audar-ASR-V1-Turbo|ResNet(?:34|152)|3D-Speaker|\bm[1-6]\b"
                   r"|\b(?:16|8) kHz\b|G\.711|95%|Nemotron-3-Diarization")
PUBLIC = ("summary/", "arzen/", "ami/")  # the kinds of data-ref whose numbers the docs' tables publish

_spec = importlib.util.spec_from_file_location("charts", ROOT / "docs" / "charts.py")
charts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(charts)

SUMMARY = {(r["set"], r["audio"], r["model"], r["config"]): r
           for r in json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))}
SPEAKER_SCORES = {"arzen": json.loads(ARZEN_PATH.read_text(encoding="utf-8"))["scores"],
                  "ami": json.loads(AMI_PATH.read_text(encoding="utf-8"))}
DATA = json.loads(DATA_PATH.read_text(encoding="utf-8"), parse_float=Decimal)


def pooled(corpus, system, metric, collar=0.0):
    """A speaker-label number pooled over a public set's recordings as bench/compare_ami.py and
    bench/compare_arzen.py print it for the tables, with the number of people estimated: the value in
    the chart's units (percent for shares) and the text shown."""
    rows = [r for r in SPEAKER_SCORES[corpus]
            if r["system"] == system and r["mode"] == "estimated" and r.get("collar_s", 0.0) == collar]
    total = lambda key: sum(r[key] for r in rows)
    if metric == "count_right":
        right = sum(r["found"] == r["people"] for r in rows)
        return right, str(right)
    if metric == "seconds":
        value = total("seconds") / total("audio_s") * 600
        return value, f"{value:.0f}"
    if metric == "der":
        part, whole = sum(r["missed"] + r["false_alarm"] + r["confusion"] for r in rows), total("total")
    elif metric == "wrong_on_speech":
        part, whole = total("wrong_on_speech"), total("speech")
    elif metric == "overlap_missed":
        part, whole = total("overlap_missed"), max(1, total("overlap_speaker"))
    else:  # a part of DER
        part, whole = total(metric), total("total")
    share = part / whole
    return share * 100, format(share, ".1%")[:-1]


def resolve(ref):
    """The value in the chart's units and the text shown, for a data-ref: summary numbers rounded as
    in the docs' tables (bench/score.py), ArzEn and AMI numbers pooled as in theirs, charts-data.json
    numbers as written there."""
    kind, *path = ref.split("/")
    if kind in SPEAKER_SCORES:
        return pooled(kind, *path)
    if kind == "summary":
        row = SUMMARY[tuple(path[:4])]
        metric, index = path[4], path[5:]
        value = row[metric][int(index[0])] if index else row[metric]
        if metric == "rtf":
            return value, f"{value:.2f}"
        return value * 100, format(value, ".1%" if metric == "wer" else ".0%")[:-1]
    assert kind == "data", ref
    block = DATA[path[0]]
    value = block["values"][path[1]] if len(path) == 2 else block["rows"][path[1]][block["columns"].index(path[2])]
    return float(value), str(value)


def section(doc, heading=None):
    """The lines of a markdown file, or of the section under one heading (given by its text)."""
    lines, fenced, heads = (ROOT / doc).read_text(encoding="utf-8").splitlines(), False, []
    for i, line in enumerate(lines):
        fenced ^= line.startswith("```")
        if not fenced and re.match(r"#{1,6} ", line):
            heads.append((i, len(line) - len(line.lstrip("#")), line.lstrip("#").strip()))
    if heading is None:
        return lines
    starts = [(i, level) for i, level, text in heads if text == heading]
    assert len(starts) == 1, f"{doc}: heading {heading!r}"
    start, level = starts[0]
    end = next((i for i, lv, _ in heads if i > start and lv <= level), len(lines))
    return lines[start + 1:end]


def tables(doc, heading=None):
    """The markdown tables in a file or one of its sections: lists of rows of stripped cells, header first."""
    found, rows = [], []
    for line in section(doc, heading) + [""]:
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip()[1:-1].split("|")])
        elif rows:
            found.append([rows[0]] + rows[2:])
            rows = []
    return found


def rows_named(doc, first_cell, heading=None):
    """(header, row) for every table row in doc (or one section) whose first cell is first_cell."""
    return [(table[0], row) for table in tables(doc, heading) for row in table[1:] if row[0] == first_cell]


def flat(doc):
    return " ".join((ROOT / doc).read_text(encoding="utf-8").split())


def free_text(el):
    """The text of an element, leaving out numbers that are marked with their source or as ticks."""
    if el.get("data-ref") or el.get("data-tick"):
        return ""
    return (el.text or "") + "".join(free_text(child) + (child.tail or "") for child in el)


class ChartsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svgs = charts.build()
        cls.roots = {name: ET.fromstring(svg) for name, svg in cls.svgs.items()}

    def test_committed_charts_are_the_regenerated_ones(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(charts, "OUT", Path(tmp)), \
                contextlib.redirect_stdout(io.StringIO()):
            charts.main()
            written = {p.name: p.read_bytes() for p in Path(tmp).glob("*.svg")}
        committed = {p.name: p.read_bytes() for p in CHART_DIR.glob("*.svg")}
        self.assertEqual(sorted(written), sorted(committed))
        for name, content in written.items():
            self.assertEqual(content, committed[name], f"{name} is out of date: run python docs/charts.py")
        self.assertEqual(set(written), set(self.svgs))

    def test_charts_are_well_formed_svg(self):
        for name, svg in self.svgs.items():
            with self.subTest(name):
                root = ET.fromstring(svg)
                self.assertEqual(root.tag, SVG + "svg")
                self.assertEqual(root.get("width"), "800")
                self.assertEqual(root.get("viewBox"), f"0 0 800 {root.get('height')}")
                self.assertTrue(root.findtext(SVG + "title").strip())
                background = root.find(SVG + "rect")  # the white card that keeps it legible in dark mode
                self.assertEqual((background.get("fill"), background.get("rx")), ("#ffffff", "12"))
                self.assertNotIn("—", svg)  # plain text, no em dashes
                self.assertNotIn("<script", svg)

    def test_every_number_drawn_equals_its_source(self):
        for name, root in self.roots.items():
            with self.subTest(name):
                numbers = [el for el in root.iter() if el.tag in (SVG + "text", SVG + "tspan") and el.get("data-ref")]
                self.assertGreater(len(numbers), 5)
                for el in numbers:
                    self.assertEqual("".join(el.itertext()), resolve(el.get("data-ref"))[1], el.get("data-ref"))

    def test_public_numbers_are_the_ones_in_the_docs_tables(self):
        checked = 0
        for name, root in self.roots.items():
            for el in root.iter():
                ref, where = el.get("data-ref") or el.get("data-lo") or "", el.get("data-doc")
                if el.tag == SVG + "path" or not ref.startswith(PUBLIC):
                    continue
                with self.subTest(name, ref=ref):
                    self.assertTrue(where, "a public number without the place the docs publish it")
                    doc, *place = where.split("|")
                    doc, _, heading = doc.partition("#")
                    if len(place) == 1:  # a sentence
                        self.assertIn(place[0], flat(doc))
                        published = NUMBER.findall(place[0])
                    else:  # a table cell in a section: the row's first cell and the column's header
                        cells = [row[header.index(place[1])] for header, row in rows_named(doc, place[0], heading)
                                 if place[1] in header]
                        self.assertEqual(len(cells), 1, where)
                        published = NUMBER.findall(cells[0])
                    shown = [resolve(el.get(a))[1] for a in ("data-lo", "data-hi")] if el.get("data-lo") \
                        else ["".join(el.itertext())]
                    for s in shown:
                        self.assertIn(s, published, where)
                        checked += 1
        self.assertGreater(checked, 25)

    def test_bars_whiskers_and_ticks_sit_at_their_values(self):
        for name, root in self.roots.items():
            with self.subTest(name):
                bars = [el for el in root.iter(SVG + "path") if el.get("data-ref")]
                in_panels = 0
                for g in root.iter(SVG + "g"):
                    self.assertEqual(g.get("data-axis"), "x")
                    v0, v1, p0, p1 = map(float, g.get("data-scale").split())
                    at = lambda v: p0 + (v - v0) * (p1 - p0) / (v1 - v0)
                    for el in g:
                        tag, tick = el.tag[len(SVG):], el.get("data-tick")
                        if tag == "path" and el.get("data-ref"):
                            xs = [float(x) for x, _ in re.findall(r"(-?[\d.]+),(-?[\d.]+)", el.get("d"))]
                            # a part of a stacked bar starts where the parts before it end
                            base = sum(resolve(r)[0] for r in el.get("data-stack", "").split("|") if r)
                            self.assertAlmostEqual(min(xs), at(base), delta=0.02)
                            self.assertAlmostEqual(max(xs), at(base + resolve(el.get("data-ref"))[0]), delta=0.02)
                            in_panels += 1
                        elif tag == "line" and el.get("data-lo"):
                            self.assertAlmostEqual(float(el.get("x1")), at(resolve(el.get("data-lo"))[0]), delta=0.02)
                            self.assertAlmostEqual(float(el.get("x2")), at(resolve(el.get("data-hi"))[0]), delta=0.02)
                        elif tag == "line" and tick:
                            for x in ("x1", "x2"):
                                self.assertAlmostEqual(float(el.get(x)), at(float(tick)), delta=0.02)
                        elif tag == "text" and tick:
                            self.assertEqual(float(el.text), float(tick))
                            self.assertAlmostEqual(float(el.get("x")), at(float(tick)), delta=0.02)
                self.assertEqual(in_panels, len(bars))
                self.assertGreater(len(bars), 5)

    def test_no_number_is_drawn_without_a_source(self):
        for name, root in self.roots.items():
            for el in [*root.iter(SVG + "text"), *root.iter(SVG + "title")]:
                with self.subTest(name, text="".join(el.itertext())):
                    self.assertNotRegex(NAMES.sub("", free_text(el)), r"\d")

    def test_charts_data_holds_the_numbers_the_docs_publish(self):
        for name, block in DATA.items():
            if not isinstance(block, dict):
                continue
            doc = block["published"].split(",")[0]
            with self.subTest(name):
                if "quote" in block:
                    self.assertIn(block["quote"], flat(doc))
                    published = iter(NUMBER.findall(block["quote"]))
                    for key, value in block["values"].items():
                        self.assertIn(str(value), published, key)  # consumes the iterator: in this order
                    continue
                for first_cell, values in block["rows"].items():
                    self.assertEqual(len(values), len(block["columns"]), first_cell)
                    rows = rows_named(doc, first_cell)
                    self.assertEqual(len(rows), 1, f"{first_cell!r} in {doc}")
                    published = [n for cell in rows[0][1][1:] for n in (NUMBER.findall(cell) or [None])]
                    self.assertEqual(published, [None if v is None else str(v) for v in values], first_cell)

    def test_docs_show_every_chart_with_alt_text(self):
        shown = set()
        for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            for alt, target in re.findall(r"!\[([^\]]*)\]\(([^)\s]*images/charts/[^)\s]+)\)", doc.read_text(encoding="utf-8")):
                with self.subTest(doc.name, chart=target):
                    self.assertTrue((doc.parent / target).is_file())
                    self.assertGreater(len(alt.split()), 5)
                    shown.add(Path(target).name)
        self.assertEqual(shown, set(self.svgs))

    def test_generator_reads_only_summary_json_and_the_published_numbers(self):
        read, real = [], Path.read_text

        def spy(path, *args, **kwargs):
            read.append(Path(path).resolve())
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", spy), \
                mock.patch("builtins.open", side_effect=AssertionError("unexpected open()")):
            charts.build()
        self.assertEqual(set(read), {SUMMARY_PATH, ARZEN_PATH, AMI_PATH, DATA_PATH})

    def test_speaker_label_tables_are_the_pooled_results(self):
        """Every number of the ArzEn and AMI tables in docs/04-speaker-labels.md, pooled again from
        the scores that bench/compare_arzen.py and bench/compare_ami.py keep in results/."""
        systems = {row: system for system, row in charts.SPEAKER_ROWS.items()}
        for corpus, place in charts.SPEAKER_TABLES.items():
            doc, _, heading = place.partition("#")
            (table,) = tables(doc, heading)
            self.assertEqual([row[0] for row in table[1:]], list(systems))
            for row in table[1:]:
                system = systems[row[0]]
                show = lambda metric, collar=0.0: pooled(corpus, system, metric, collar)[1]
                recordings = len({r.get("recording") or r["meeting"] for r in SPEAKER_SCORES[corpus]})
                expected = {  # each column's cell, worked out only for the columns the table has
                    "Wrong speaker on speech": lambda: "" if "overlap" in system else f"{show('wrong_on_speech')}%",
                    "DER": lambda: f"{show('der')}%",
                    "Missed / false alarm / confusion": lambda: " / ".join(
                        f"{show(part)}%" for part in ("missed", "false_alarm", "confusion")),
                    "DER with a 0.25 s collar": lambda: f"{show('der', 0.25)}%",
                    "Speaker time missed in overlaps": lambda: f"{show('overlap_missed')}%",
                    "Count estimated right": lambda: f"{show('count_right')} of {recordings}",
                    "Seconds per 10 minutes": lambda: show("seconds")}
                with self.subTest(corpus, system=system):
                    self.assertEqual(dict(zip(table[0][1:], row[1:])), {c: expected[c]() for c in table[0][1:]})

    def test_result_tables_are_written_from_summary_json(self):
        spec = importlib.util.spec_from_file_location("score", ROOT / "bench" / "score.py")
        score = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(score)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tables.md"
            score.write_tables(json.loads(SUMMARY_PATH.read_text(encoding="utf-8")), path)
            written = path.read_text(encoding="utf-8")
        self.assertEqual(written, (ROOT / "docs" / "results-tables.md").read_text(encoding="utf-8"),
                         "docs/results-tables.md is out of date: rewrite it with bench/score.py's write_tables")
        main, _, also = written.partition("\n## Also tried")
        for model in ("R2T2", "Qwen3-ASR", "Audar"):  # the models also tried come last, in their own table
            self.assertNotIn(f"| {model}", main)
            self.assertIn(f"| {model}", also)
        self.assertNotIn("—", written)

    def test_generator_uses_only_the_standard_library(self):
        tree = ast.parse((ROOT / "docs" / "charts.py").read_text(encoding="utf-8"))
        modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        modules |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertLessEqual({m.split(".")[0] for m in modules}, set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
