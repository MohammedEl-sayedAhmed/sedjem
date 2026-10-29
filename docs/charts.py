#!/usr/bin/env python3
"""Draw the charts in the docs as SVG files in docs/images/charts/: python docs/charts.py

Standard library only; the SVG is written by hand. Public test-set numbers come from
results/summary.json, which bench/score.py writes, and for speaker labels from
results/arzen-diarization.json and results/ami-diarization.json, which bench/compare_arzen.py and
bench/compare_ami.py write. The real meetings are private, so their numbers are never read from
results/meetings/ or data/: docs/charts-data.json holds the aggregate numbers already published in
docs/03-results.md and docs/04-speaker-labels.md.

Every number drawn names its source in a data-ref attribute, and public numbers also name the docs
table cell or sentence that publishes them (data-doc). Each panel records its scale (data-scale), and
each part of a stacked bar the parts before it (data-stack), so tests/test_charts.py can check the
labels, the bar lengths and the whiskers against the sources and the docs. The folder
docs/images/charts/ is written by this script only.
"""
import json
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "results" / "summary.json"
ARZEN, AMI = ROOT / "results" / "arzen-diarization.json", ROOT / "results" / "ami-diarization.json"
DATA = ROOT / "docs" / "charts-data.json"
OUT = ROOT / "docs" / "images" / "charts"
# Where the docs publish the public numbers: "file#heading", the section that holds the table
RESULTS, README, SPEAKERS = "docs/03-results.md", "README.md", "docs/04-speaker-labels.md"
PERLE_TABLE, PHONE_TABLE = f"{RESULTS}#Public benchmark: Perle at 16 kHz", f"{RESULTS}#Phone audio"
MODELS_TABLE = f"{README}#Models"
SPEAKER_TABLES = {"arzen": f"{SPEAKERS}#ArzEn: interviews in Egyptian Arabic and English",
                  "ami": f"{SPEAKERS}#AMI: English meetings with much overlap"}

WIDTH = 800
FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
INK, MUTED, FAINT = "#1f2937", "#4b5563", "#6b7280"          # text
GRID, BASELINE, WHISKER = "#e5e7eb", "#9ca3af", "#374151"    # chrome
COHERE, COHERE_PALE = "#0e7c7b", "#7fb9b8"                   # the app's accent teal
WHISPER = "#2563eb"
OTHER, OTHER_PALE = "#8a94a3", "#aeb5c0"                     # the other models; paler: whole clip
SLATE, SLATE_PALE = "#4b5563", "#aab2bd"                     # voiceprint models
ELEVENLABS = "#b4524a"
NEMOTRON = "#5e8c14"                                         # NVIDIA's green, darkened to read on white
MISSED, FALSE_ALARM, CONFUSION = SLATE_PALE, "#5f6b7a", "#9b3fb5"  # the parts of DER
HALO = dict(stroke="#ffffff", stroke_width=3, stroke_linejoin="round", paint_order="stroke")  # lines stop short of values

COHERE_MODEL = "cohere-transcribe-arabic-07-2026-Q4_K_M"
# The three leading models: chart label, results/summary.json model and config, colour, and the
# first cell of their rows in the Perle and phone tables of docs/03-results.md and in the README
LEADING = [
    dict(label="Cohere Transcribe Arabic", model=COHERE_MODEL, config="ar", color=COHERE,
         perle="Cohere Transcribe Arabic (Q4, transcribe.cpp)", phone="Cohere Transcribe Arabic",
         readme="Cohere Transcribe Arabic"),
    dict(label="whisper-medium (default)", model="whisper-whisper-medium-arabic-codeswitched-ct2",
         config="ar", color=WHISPER, perle="whisper-medium code-switching fine-tune (default)",
         phone="whisper-medium code-switching", readme="whisper-medium code-switching (default)"),
    dict(label="Whisper large-v3 + hint", model="whisper-large-v3", config="ar-p3c2e92", color=OTHER,
         perle="Whisper large-v3 + style hint", phone="Whisper large-v3 + hint",
         readme="Whisper large-v3 with an Egyptian style hint"),
]
# The models that were also tried (R2T2, Qwen3-ASR, Audar) stay in the tables, not in the charts
EXCERPTS = ["m1", "m2", "m3", "m4", "m5", "m6"]
# The speaker models of Settings → Models: chart label, colour, their rows in docs/04-speaker-labels.md
# (the voiceprint and Nemotron tables on the meeting excerpts) and their system in
# results/*-diarization.json, which the ArzEn and AMI tables name in the docs' rows of SPEAKER_ROWS
SPEAKER_MODELS = [
    dict(label="TitaNet-small (default)", color=SLATE, meetings=("voiceprints", "TitaNet-small (default)"),
         system="TitaNet-small"),
    dict(label="TitaNet-large", color=SLATE_PALE, meetings=("voiceprints", "TitaNet-large"),
         system="TitaNet-large"),
    dict(label="3D-Speaker CAM++", color=SLATE_PALE, meetings=("voiceprints", "3D-Speaker CAM++ (Chinese and English)"),
         system="3D-Speaker CAM++"),
    dict(label="Nemotron-3-Diarization", color=NEMOTRON, meetings=("nemotron", "Nemotron-3-Diarization"),
         system="Nemotron, as the app"),
]
SPEAKER_ROWS = {"TitaNet-small": "TitaNet-small voiceprints (default)", "TitaNet-large": "TitaNet-large voiceprints",
                "3D-Speaker CAM++": "3D-Speaker CAM++ voiceprints",
                "Nemotron, as the app": "Nemotron, one speaker at a time (as the app)",
                "Nemotron, overlap included": "Nemotron, overlaps included"}


# ---------------------------------------------------------------------------------------------
# Numbers and where they come from
# ---------------------------------------------------------------------------------------------

class Num:
    """A number to draw: its value in the chart's units, the text shown, its source (data-ref)
    and, for numbers from summary.json, where the docs publish it (data-doc)."""

    def __init__(self, value, shown, ref, doc=None):
        self.value, self.shown, self.ref, self.doc = value, shown, ref, doc

    def tag(self, unit=""):
        """The number as SVG markup, followed by its unit."""
        return f"<tspan{attrs(data_ref=self.ref, data_doc=self.doc)}>{escape(self.shown)}</tspan>{escape(unit)}"


class Sources:
    """The numbers the charts may draw: results/summary.json, the two speaker-label sets in
    results/*-diarization.json, and docs/charts-data.json."""

    def __init__(self):
        rows = json.loads(SUMMARY.read_text(encoding="utf-8"))
        self.summary = {(r["set"], r["audio"], r["model"], r["config"]): r for r in rows}
        self.speakers = {"arzen": json.loads(ARZEN.read_text(encoding="utf-8"))["scores"],
                         "ami": json.loads(AMI.read_text(encoding="utf-8"))}
        self.data = json.loads(DATA.read_text(encoding="utf-8"), parse_float=Decimal)

    def perle(self, audio, model, config, metric, doc):
        """A Perle number from summary.json, rounded as bench/score.py rounds it for the tables.
        Shares are drawn in percent; metric "wer_ci/0" and "wer_ci/1" are the interval's ends."""
        key = ("perle", audio, model, config)
        name, _, index = metric.partition("/")
        raw = self.summary[key][name]
        raw = raw[int(index)] if index else raw
        ref = "/".join(("summary",) + key + (metric,))
        if name == "rtf":
            return Num(raw, f"{raw:.2f}", ref, doc)
        return Num(raw * 100, format(raw, ".1%" if name == "wer" else ".0%")[:-1], ref, doc)

    def speaker(self, corpus, system, metric, column):
        """A number of the ArzEn or AMI speaker-label table ("arzen" or "ami"), pooled over the
        recordings with the number of people estimated and no collar, as bench/compare_arzen.py and
        bench/compare_ami.py pool it for the tables. Shares are drawn in percent; "seconds" is the
        time per 10 minutes of audio and "count_right" the recordings whose count was right. The
        docs publish it in the table's row for the system, in the given column."""
        rs = [r for r in self.speakers[corpus]
              if r["system"] == system and r["mode"] == "estimated" and not r.get("collar_s")]
        ref, doc = "/".join((corpus, system, metric)), f"{SPEAKER_TABLES[corpus]}|{SPEAKER_ROWS[system]}|{column}"
        if metric == "count_right":
            right = sum(r["found"] == r["people"] for r in rs)
            return Num(right, str(right), ref, doc)
        if metric == "seconds":
            v = sum(r["seconds"] for r in rs) / sum(r["audio_s"] for r in rs) * 600
            return Num(v, f"{v:.0f}", ref, doc)
        if metric == "der":
            v = sum(r["missed"] + r["false_alarm"] + r["confusion"] for r in rs) / sum(r["total"] for r in rs)
        elif metric == "wrong_on_speech":
            v = sum(r["wrong_on_speech"] for r in rs) / sum(r["speech"] for r in rs)
        elif metric == "overlap_missed":
            v = sum(r["overlap_missed"] for r in rs) / max(1, sum(r["overlap_speaker"] for r in rs))
        else:  # a part of DER: missed, false_alarm or confusion
            v = sum(r[metric] for r in rs) / sum(r["total"] for r in rs)
        return Num(v * 100, format(v, ".1%")[:-1], ref, doc)

    def published(self, block, row, column=None):
        """A number from charts-data.json, shown as written there: a table block's row and column,
        or a quote block's value by name. None for an empty cell."""
        b = self.data[block]
        if column is None:
            raw, path = b["values"][row], (block, row)
        else:
            raw, path = b["rows"][row][b["columns"].index(column)], (block, row, column)
        return None if raw is None else Num(float(raw), str(raw), "/".join(("data",) + path))


# ---------------------------------------------------------------------------------------------
# SVG pieces
# ---------------------------------------------------------------------------------------------

def num(v):
    """A number for an attribute: at most two decimals, no trailing zeros."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def attrs(**kw):
    """XML attributes; a trailing _ is dropped and other _ become -. None leaves one out."""
    return "".join(f" {k.rstrip('_').replace('_', '-')}={quoteattr(v if isinstance(v, str) else num(v))}"
                   for k, v in kw.items() if v is not None)


def text(x, y, markup, size=12, fill=INK, **kw):
    return f"<text{attrs(x=x, y=y, font_size=size, fill=fill, **kw)}>{markup}</text>"


def line(x1, y1, x2, y2, stroke=GRID, **kw):
    return f"<line{attrs(x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, **kw)}/>"


def bar(x0, x1, y, h, fill, ref, rounded=True, **kw):
    """A horizontal bar from x0 to x1: square at the baseline, rounded at the value's end (unless it
    is a part of a stacked bar that another part follows)."""
    r = min(4, (x1 - x0) / 2, h / 2) if rounded else 0
    p = lambda x, y_: f"{num(x)},{num(y_)}"
    d = (f"M{p(x0, y)} L{p(x1 - r, y)} Q{p(x1, y)} {p(x1, y + r)} L{p(x1, y + h - r)} "
         f"Q{p(x1, y + h)} {p(x1 - r, y + h)} L{p(x0, y + h)} Z")
    return f"<path{attrs(d=d, fill=fill, data_ref=ref, **kw)}/>"


def stacked(s, parts, y, h):
    """A bar made of parts (dicts with n and color) laid end to end from 0, each part separated from
    the next by a 2 px gap in the background colour. Each part names the parts before it (data-stack)."""
    out, below, base = [], [], 0
    for k, part in enumerate(parts):
        end = base + part["n"].value
        out.append(bar(s(base), s(end), y, h, part["color"], part["n"].ref, rounded=k == len(parts) - 1,
                       data_stack="|".join(below) or None))
        below.append(part["n"].ref)
        base = end
    base = 0
    for part in parts[:-1]:
        base += part["n"].value
        out.append(line(s(base), y - 0.5, s(base), y + h + 0.5, "#ffffff", stroke_width=2))
    return out


def text_width(s, size):
    """Roughly how wide s is at size px in the fonts of FONT (only used to space a legend)."""
    widths = {**dict.fromkeys("ijlI.,:;'!|", 0.28), **dict.fromkeys("frt()[]- ", 0.36),
              **dict.fromkeys("mwMW%", 0.86)}
    return size * sum(widths.get(c, 0.66 if c.isupper() else 0.56) for c in s)


class Scale:
    """Maps the values v0..v1 linearly onto the pixels p0..p1."""

    def __init__(self, v0, v1, p0, p1):
        self.v0, self.v1, self.p0, self.p1 = v0, v1, p0, p1

    def __call__(self, v):
        return self.p0 + (v - self.v0) * (self.p1 - self.p0) / (self.v1 - self.v0)

    def group(self):
        scale = " ".join(num(x) for x in (self.v0, self.v1, self.p0, self.p1))
        return f"<g{attrs(data_axis='x', data_scale=scale)}>"


def header(title, lines):
    out, y = [text(24, 36, escape(title), size=16, font_weight="600")], 36
    for s in lines:
        y += 20
        out.append(text(24, y, escape(s), size=12, fill=MUTED))
    return out, y


def legend(items, y):
    out, x = [], 24
    for color, label in items:
        out.append(f"<rect{attrs(x=x, y=y - 10, width=12, height=12, rx=3, fill=color)}/>")
        out.append(text(x + 18, y, escape(label), size=12, fill=MUTED))
        x += 18 + text_width(label, 12) + 24
    return out


def svg(title, height, body):
    height = int(height + 0.999)
    return "\n".join([
        f"<svg{attrs(xmlns='http://www.w3.org/2000/svg', width=WIDTH, height=height)}"
        f" viewBox=\"0 0 {WIDTH} {height}\" role=\"img\" font-family={quoteattr(FONT)}>",
        f"<title>{escape(title)}</title>",
        f"<rect{attrs(x=0.5, y=0.5, width=WIDTH - 1, height=height - 1, rx=12, fill='#ffffff', stroke=GRID)}/>",
        *body, "</svg>", ""])


def ticks(scale, values, spans, bottom, fmt=lambda t: num(t), size=11):
    """Gridlines behind each block of rows, and the tick labels under the last one."""
    out = []
    for t in values:
        x = scale(t)
        for top, end in spans:
            out.append(line(x, top, x, end, BASELINE if t == 0 else GRID, data_tick=num(t)))
        out.append(text(x, bottom + 16, escape(fmt(t)), size=size, fill=FAINT, text_anchor="middle",
                        data_tick=num(t)))
    return out


def bar_chart(title, lines, rows, panels, legend_items=(), label_x=196):
    """Rows of horizontal bars in one or more panels side by side.

    panels: dicts with scale, ticks, fmt (formats a tick), title and note (a heading over the
    panel), unit (after each value), mark ((value, label): a dashed line across the rows) and
    axis (a label under the ticks); or text=True and x for a column of text.
    rows: dicts with label, sub (markup under the label), group (a heading before the row),
    rule (a line before it) and cells, one per panel: a list of bars (empty for none), each a dict
    with n (Num), color and optionally lo and hi (Num, a whisker), tag (a label left of the bar),
    after (markup after the value) and parts (dicts with n and color: a stacked bar, n its total);
    or markup, for a text panel.
    """
    out, y = header(title, lines)
    if legend_items:
        y += 26
        out += legend(legend_items, y)
    y += 16
    if any(p.get("title") for p in panels):
        for p in panels:
            x, anchor = (p["x"], "middle") if p.get("text") else (p["scale"].p0, None)
            out.append(text(x, y + 14, escape(p["title"]), size=13, font_weight="600", text_anchor=anchor))
            out.append(text(x, y + 29, escape(p["note"]), size=11, fill=FAINT, text_anchor=anchor))
        y += 40
    if any(p.get("mark") for p in panels) and not rows[0].get("group"):
        y += 18  # room for the mark's label, which otherwise sits beside the first group heading
    marks, spans, start = [[] for _ in panels], [], y
    for i, row in enumerate(rows):
        if row.get("group") or row.get("rule"):
            if i:
                spans.append((start, y))
                out.append(line(24, y + 6, WIDTH - 24, y + 6))
            y += 12
            if row.get("group"):
                out.append(text(24, y + 10, escape(row["group"]), size=11, fill=FAINT, font_weight="600"))
                y += 18
            start = y
        bars = [c for c in row["cells"] if isinstance(c, list)]
        n, tagged = max(map(len, bars)), any(b.get("tag") for c in bars for b in c)
        h, gap, pad = (16, 0, 7) if n == 1 else (14, 5, 10) if tagged else (12, 2, 7)
        height = n * h + (n - 1) * gap + 2 * pad
        mid = y + height / 2
        if row.get("sub"):
            out.append(text(label_x, mid - 2, escape(row["label"]), size=13, text_anchor="end"))
            out.append(text(label_x, mid + 12, row["sub"], size=11, fill=FAINT, text_anchor="end"))
        else:
            out.append(text(label_x, mid + 4.5, escape(row["label"]), size=13, text_anchor="end"))
        for p, cell, mark in zip(panels, row["cells"], marks):
            if p.get("text"):
                mark.append(text(p["x"], mid + 4.5, cell, size=12, text_anchor="middle"))
                continue
            s = p["scale"]
            for j, b in enumerate(cell):
                top = y + pad + (j + (n - len(cell)) / 2) * (h + gap)  # fewer bars than the row has: centred
                mid_bar, end = top + h / 2, s(b["n"].value)
                if b.get("parts"):
                    mark += stacked(s, b["parts"], top, h)
                else:
                    mark.append(bar(s(0), end, top, h, b["color"], b["n"].ref))
                if b.get("lo"):
                    lo, hi = s(b["lo"].value), s(b["hi"].value)
                    mark.append(line(lo, mid_bar, hi, mid_bar, WHISKER, stroke_width=1.5,
                                     data_lo=b["lo"].ref, data_hi=b["hi"].ref, data_doc=b["lo"].doc))
                    for x in (lo, hi):
                        mark.append(line(x, mid_bar - 5, x, mid_bar + 5, WHISKER, stroke_width=1.5))
                    end = max(end, hi)
                if b.get("tag"):
                    mark.append(text(s(0) - 8, mid_bar + 4, escape(b["tag"]), size=11, fill=MUTED,
                                     text_anchor="end"))
                size = 12 if h >= 14 else 11
                mark.append(text(end + 6, mid_bar + size * 0.36, b["n"].tag(p.get("unit", "")) + b.get("after", ""),
                                 size=size, **HALO))
        y += height
    spans.append((start, y))
    for p, mark in zip(panels, marks):
        if p.get("text"):
            out += mark
            continue
        s = p["scale"]
        out.append(s.group())
        out += ticks(s, p["ticks"], spans, y, p.get("fmt", num))
        if p.get("mark"):
            value, label = p["mark"]
            x = s(value)
            out.append(line(x, spans[0][0] - 12, x, y, WHISKER, stroke_width=1.2,
                            stroke_dasharray="4 3", data_tick=num(value)))
            out.append(text(x + 5, spans[0][0] - 4, escape(label), size=11, fill=MUTED))
        out += mark
        out.append("</g>")
        if p.get("axis"):
            out.append(text((s.p0 + s.p1) / 2, y + 36, escape(p["axis"]), size=11, fill=MUTED,
                            text_anchor="middle"))
    bottom = y + (46 if any(p.get("axis") for p in panels) else 30) + 12
    return svg(title, bottom, out)


def percent_panel(p0, p1, top, step, title, note):
    return dict(scale=Scale(0, top, p0, p1), ticks=range(0, top + 1, step), title=title, note=note,
                unit="%")


# ---------------------------------------------------------------------------------------------
# The charts
# ---------------------------------------------------------------------------------------------

def models_chart(src):
    """README, Models: word error rate and English kept for the three local models."""
    cell = lambda m, metric, column: [dict(n=src.perle("wav16k", m["model"], m["config"], metric,
                                                        f"{MODELS_TABLE}|{m['readme']}|{column}"), color=m["color"])]
    rows = [dict(label=m["label"], cells=[cell(m, "wer", "Word error rate, Egyptian test set"),
                                          cell(m, "en_kept", "English terms kept in English")])
            for m in LEADING]
    panels = [percent_panel(206, 456, 25, 5, "Word error rate, %", "lower is better"),
              percent_panel(540, 740, 100, 25, "English terms kept, %", "higher is better")]
    return bar_chart("The models that run on your computer",
                     ["The Egyptian Arabic–English clips of the Perle benchmark, at 16 kHz."], rows, panels)


def perle_chart(src):
    """docs/03-results.md, Perle at 16 kHz: WER with its confidence interval, and English kept."""
    def row(m):
        doc = lambda column: f"{PERLE_TABLE}|{m['perle']}|{column}"
        get = lambda metric, column: src.perle("wav16k", m["model"], m["config"], metric, doc(column))
        wer = dict(n=get("wer", "WER (95% CI)"), color=m["color"], lo=get("wer_ci/0", "WER (95% CI)"),
                   hi=get("wer_ci/1", "WER (95% CI)"))
        return dict(label=m["label"], cells=[[wer], [dict(n=get("en_kept", "English kept"), color=m["color"])]])

    panels = [percent_panel(206, 506, 30, 5, "Word error rate, %", "lower is better"),
              percent_panel(590, 750, 100, 25, "English kept, %", "higher is better")]
    rows = [row(m) for m in LEADING]
    return bar_chart("Perle at 16 kHz: word error rate and English kept",
                     ["Whiskers show the 95% confidence interval over the clips."], rows, panels)


def phone_chart(src):
    """docs/03-results.md, Phone audio: WER of the three leading models in the three conditions."""
    conditions = [("wav16k", "16 kHz"), ("phone", "Through 8 kHz"), ("g711", "G.711 telephone")]
    rows = [dict(label=m["label"], cells=[[
        dict(n=src.perle(audio, m["model"], m["config"], "wer", f"{PHONE_TABLE}|{m['phone']}|{column}"),
             color=m["color"], tag=column) for audio, column in conditions]]) for m in LEADING]
    panels = [percent_panel(330, 700, 25, 5, "Word error rate, %", "lower is better")]
    return bar_chart("Phone audio: word error rate on the Perle clips",
                     ["The same clips at 16 kHz, resampled through 8 kHz, and through a G.711 telephone channel."],
                     rows, panels)


def speed_chart(src):
    """docs/03-results.md (On a graphics card) and docs/05-command-line.md: speed per model."""
    def perle(label, m, color, doc, config=None, group=None):
        n = src.perle("wav16k", m["model"], config or m["config"], "rtf", doc)
        return dict(label=label, group=group, cells=[[dict(n=n, color=color)]])

    def meeting(label, engine, color, group=None):
        get = lambda name: src.published("meeting_speed", f"{engine}_{name}")
        lo, hi = get("min"), get("max")
        after = f"<tspan{attrs(fill=FAINT)}> ({lo.tag()}–{hi.tag()})</tspan>"
        return dict(label=label, group=group, cells=[[dict(n=get("mean"), color=color, lo=lo, hi=hi, after=after)]])

    cohere, whisper, large = LEADING
    speed = lambda m: f"{PERLE_TABLE}|{m['perle']}|Speed"
    rows = [perle("Cohere, integrated GPU", cohere, COHERE, f"{RESULTS}|at 0.15 times real time instead of 0.36",
                  config="ar-gpu", group="Perle clips"),
            perle("Cohere, CPU", cohere, COHERE, speed(cohere)),
            perle("whisper-medium", whisper, WHISPER, speed(whisper)),
            perle("Whisper large-v3 + hint", large, OTHER, speed(large)),
            meeting("Cohere", "cohere", COHERE, group="Meeting excerpts with speaker labels: mean and range"),
             meeting("whisper-medium", "whisper", WHISPER)]
    panels = [dict(scale=Scale(0, 2.5, 252, 702), ticks=[0, 0.5, 1, 1.5, 2, 2.5],
                   fmt=lambda t: "0" if t == 0 else f"{t:.1f}", mark=(1, "real time"),
                   axis="processing time / audio length")]
    return bar_chart("Speed: processing time per second of audio",
                     ["Lower is faster; left of the dashed line is faster than real time.",
                      "All on the test laptop's CPU, except the integrated GPU row."],
                     rows, panels, label_x=240)


def meetings_chart(src):
    """docs/03-results.md, Real meetings: WER against ElevenLabs and English kept, per excerpt."""
    rows = []
    for m in EXCERPTS + ["pooled"]:
        get = lambda column, color: dict(n=src.published("meetings", m, column), color=color)
        pooled = m == "pooled"
        rows.append(dict(label="Pooled" if pooled else m, rule=pooled,
                         sub="all six" if pooled else src.published("meetings", m, "people").tag(" people"),
                         cells=[[get("cohere_wer", COHERE), get("whisper_wer", WHISPER)],
                                [get("cohere_en_kept", COHERE), get("whisper_en_kept", WHISPER)]]))
    panels = [percent_panel(112, 462, 70, 10, "Words that differ from ElevenLabs, %", "lower is closer"),
              percent_panel(540, 740, 100, 25, "English kept, %", "higher is better")]
    return bar_chart("Real meetings: difference from ElevenLabs and English kept",
                     ["Each excerpt, then all six pooled. The reference is ElevenLabs' transcript."],
                     rows, panels, [(COHERE, "Cohere Transcribe Arabic"), (WHISPER, "whisper-medium")], label_x=96)


def variations_chart(src):
    """docs/03-results.md, Variations on Cohere: one small panel per column of the table."""
    setups = [("Default", "TitaNet-small labels, audio cut at speaker changes (the default)", COHERE),
              ("No labels", "No speaker labels", COHERE_PALE),
              ("Aligned", "TitaNet-small labels on forced-aligned words (`--align`, experimental)", COHERE_PALE),
              ("WeSpeaker", "First voiceprint model (WeSpeaker ResNet34), cut at speaker changes", COHERE_PALE)]
    metrics = [("wer", "Word error rate, %", "lower is better", 50, "%"),
               ("arabic_word_errors", "Arabic-word errors, %", "lower is better", 50, "%"),
               ("en_kept", "English kept, %", "higher is better", 100, "%"),
               ("wrong_speaker", "Wrong speaker, %", "lower is better", 30, "%"),
               ("speed", "Speed, × real time", "lower is faster", 0.5, ""),
               ("peak_memory_gb", "Peak memory, GB", "lower is better", 5, "")]
    title = "Cohere on the real meetings: four setups compared"
    out, y = header(title, ["Pooled over the six excerpts. Default: TitaNet-small labels, audio cut at speaker "
                            "changes.", "Aligned: forced alignment (--align). WeSpeaker: the first voiceprint model."])
    y += 24
    column_width, panel_height = (WIDTH - 48) / 3, 172
    for k, (column, heading, note, top, unit) in enumerate(metrics):
        px, py = 24 + (k % 3) * column_width, y + (k // 3) * panel_height
        out.append(text(px, py + 14, escape(heading), size=13, font_weight="600"))
        out.append(text(px, py + 29, escape(note), size=11, fill=FAINT))
        s, first = Scale(0, top, px + 80, px + 180), py + 40
        out.append(s.group())
        out += ticks(s, [0, top / 2, top], [(first, first + 4 * 26)], first + 4 * 26, size=10)
        for i, (short, row, color) in enumerate(setups):
            y0 = first + i * 26 + 5
            out.append(text(px + 72, y0 + 12, escape(short), size=12, text_anchor="end"))
            n = src.published("cohere_variations", row, column)
            if n is None:
                out.append(text(s(0) + 4, y0 + 12, "none", size=11, fill=FAINT))
                continue
            out.append(bar(s(0), s(n.value), y0, 16, color, n.ref))
            out.append(text(s(n.value) + 6, y0 + 12.3, n.tag(unit), size=12, **HALO))
        out.append("</g>")
    return svg(title, y + 2 * panel_height, out)


def voiceprints_chart(src):
    """docs/04-speaker-labels.md, the voiceprint table: wrong-speaker share, count given or estimated."""
    models = [("TitaNet-small (default)", "TitaNet-small (default)"),
              ("3D-Speaker CAM++", "3D-Speaker CAM++ (Chinese and English)"),
              ("TitaNet-large", "TitaNet-large"),
              ("WeSpeaker ResNet34", "WeSpeaker ResNet34 (the first default)"),
              ("WeSpeaker ResNet152", "WeSpeaker ResNet152")]
    rows = []
    for label, row in models:
        get = lambda column: src.published("voiceprints", row, column)
        rows.append(dict(label=label, sub=get("size_mb").tag(" MB"), cells=[
            [dict(n=get("wrong_speaker_true_count"), color=SLATE),
             dict(n=get("wrong_speaker_estimated_count"), color=SLATE_PALE)],
            f"{get('count_right').tag()} of {get('excerpts').tag()}"]))
    panels = [percent_panel(232, 592, 40, 10, "Words given to the wrong speaker, %", "lower is better"),
              dict(text=True, x=704, title="Speaker count", note="estimated right")]
    return bar_chart("Speaker labels: five voiceprint models",
                     ["The same words from the six meeting excerpts, labelled with each model."], rows, panels,
                     [(SLATE, "True number of speakers given"), (SLATE_PALE, "Number estimated from the voices")],
                     label_x=220)


def speakers_chart(src):
    """docs/04-speaker-labels.md: TitaNet-small against ElevenLabs' own labels, per excerpt."""
    def row(label, sub, block, key, rule=False):
        get = lambda column: src.published(block, key, column) if block == "meetings" else src.published(block, column)
        return dict(label=label, sub=sub, rule=rule, cells=[[
            dict(n=get("whisper_wrong_speaker"), color=SLATE), dict(n=get("elevenlabs_wrong_speaker"), color=ELEVENLABS)]])

    rows = [row(m, src.published("meetings", m, "people").tag(" people"), "meetings", m) for m in EXCERPTS]
    rows += [row("Pooled", "two or three people", "meetings_two_or_three_people", None, rule=True),
             row("Pooled", "all six", "meetings", "pooled")]
    panels = [percent_panel(156, 636, 40, 10, "Words given to the wrong speaker, %", "lower is better")]
    return bar_chart("Speaker labels per excerpt: TitaNet-small and ElevenLabs",
                     ["Both scored against the hand-corrected labels, which started from ElevenLabs' own."],
                     rows, panels, [(SLATE, "TitaNet-small, true number of speakers"),
                                    (ELEVENLABS, "ElevenLabs' own labels")], label_x=140)


def nemotron_excerpts_chart(src):
    """docs/04-speaker-labels.md, NVIDIA Nemotron-3-Diarization: TitaNet-small against Nemotron per excerpt."""
    titanet, estimated, nemotron = (lambda n, c=c: dict(n=n, color=c) for c in (SLATE, SLATE_PALE, NEMOTRON))
    rows = []
    for m in EXCERPTS:
        bars = [titanet(src.published("meetings", m, "whisper_wrong_speaker"))]
        if m == "m6":  # the one excerpt where TitaNet-small's estimate of the count was wrong
            bars += [estimated(src.published("nemotron_largest_excerpt", "titanet_small_estimated")),
                     nemotron(src.published("nemotron_largest_excerpt", "nemotron"))]
        else:
            bars.append(nemotron(src.published("nemotron_per_excerpt", m)))
        rows.append(dict(label=m, sub=src.published("meetings", m, "people").tag(" people"), cells=[bars]))
    pooled = lambda column, row="TitaNet-small voiceprints and clustering": src.published("nemotron", row, column)
    rows += [dict(label="Pooled", sub="two or three people", rule=True, cells=[[
                 titanet(src.published("meetings_two_or_three_people", "whisper_wrong_speaker")),
                 nemotron(src.published("nemotron_per_excerpt", "pooled_two_or_three"))]]),
             dict(label="Pooled", sub="all six", cells=[[
                 titanet(pooled("wrong_speaker_true_count")), estimated(pooled("wrong_speaker_estimated_count")),
                 nemotron(pooled("wrong_speaker_estimated_count", "Nemotron-3-Diarization"))]])]
    panels = [percent_panel(156, 636, 40, 10, "Words given to the wrong speaker, %", "lower is better")]
    return bar_chart("Meeting excerpts: TitaNet-small and Nemotron-3-Diarization",
                     ["Nemotron counted the people right on every excerpt. TitaNet-small's estimate of the count",
                      "was wrong only on m6, where it found fewer people than were there."],
                     rows, panels, [(SLATE, "TitaNet-small, true number of people"),
                                    (SLATE_PALE, "TitaNet-small, number estimated"),
                                    (NEMOTRON, "Nemotron-3-Diarization")], label_x=140)


def speaker_models_chart(src):
    """docs/04-speaker-labels.md, Conclusions: what each speaker model gives to the wrong person on
    the meeting excerpts, ArzEn and AMI, with the number of people estimated."""
    column = "Wrong speaker on speech"
    rows = [dict(label=m["label"], cells=[
        [dict(n=src.published(*m["meetings"], "wrong_speaker_estimated_count"), color=m["color"])],
        [dict(n=src.speaker("arzen", m["system"], "wrong_on_speech", column), color=m["color"])],
        [dict(n=src.speaker("ami", m["system"], "wrong_on_speech", column), color=m["color"])]])
        for m in SPEAKER_MODELS]
    panel = lambda p0, title, note: percent_panel(p0, p0 + 150, 12, 4, title, note)
    panels = [panel(212, "Meeting excerpts", "share of the words, %"),
              panel(412, "ArzEn interviews", "share of the speech, %"),
              panel(612, "AMI meetings", "share of the speech, %")]
    return bar_chart("Speaker labels on three test sets: given to the wrong person",
                     ["Lower is better. Each model estimates the number of people, and Nemotron gives one",
                      "speaker at a time, as the app uses it."], rows, panels, label_x=188)


def speaker_der_chart(src):
    """docs/04-speaker-labels.md, the public sets: DER split into its parts on ArzEn and AMI, and the
    speaker time missed where people overlap on AMI."""
    systems = [("TitaNet-small", "TitaNet-small"), ("TitaNet-large", "TitaNet-large"),
               ("3D-Speaker CAM++", "3D-Speaker CAM++"), ("Nemotron, as the app", "Nemotron, as the app"),
               ("Nemotron, with overlaps", "Nemotron, overlap included")]
    rows = []
    for corpus, group in (("arzen", "ArzEn interviews, no overlap marked"), ("ami", "AMI meetings")):
        for label, system in systems:
            get = lambda metric, column: src.speaker(corpus, system, metric, column)
            parts = [dict(n=get(metric, "Missed / false alarm / confusion"), color=color) for metric, color in
                     (("missed", MISSED), ("false_alarm", FALSE_ALARM), ("confusion", CONFUSION))]
            overlap = [dict(n=get("overlap_missed", "Speaker time missed in overlaps"), color=MISSED)] \
                if corpus == "ami" else []
            rows.append(dict(label=label, group=group if system == systems[0][1] else None,
                             cells=[[dict(n=get("der", "DER"), parts=parts)], overlap]))
    panels = [dict(scale=Scale(0, 35, 236, 516), ticks=range(0, 36, 5), title="DER and its parts, %",
                   note="lower is better", unit="%"),
              dict(scale=Scale(0, 60, 596, 736), ticks=range(0, 61, 20), title="Missed in overlaps, %",
                   note="share of the speaker time", unit="%")]
    return bar_chart("Speaker labels on the public sets: DER and its parts",
                     ["The speaker time missed, falsely detected or given to the wrong person, with the number",
                      "of people estimated. Nemotron as the app gives one speaker at a time."],
                     rows, panels, [(MISSED, "Missed speech"), (FALSE_ALARM, "False alarm"),
                                    (CONFUSION, "Wrong person (confusion)")], label_x=212)


def speaker_cost_chart(src):
    """docs/04-speaker-labels.md, Conclusions: each speaker model's time on the public sets and its size."""
    rows = []
    for m in SPEAKER_MODELS:
        time = [dict(n=src.speaker(corpus, m["system"], "seconds", "Seconds per 10 minutes"), color=m["color"],
                     tag=tag) for corpus, tag in (("arzen", "ArzEn"), ("ami", "AMI"))]
        rows.append(dict(label=m["label"], cells=[time, [dict(n=src.published(*m["meetings"], "size_mb"),
                                                               color=m["color"])]]))
    panels = [dict(scale=Scale(0, 40, 262, 482), ticks=range(0, 41, 10), title="Seconds for ten minutes of audio",
                   note="the whole speaker step, lower is faster", unit=" s"),
              dict(scale=Scale(0, 400, 556, 736), ticks=range(0, 401, 100), title="Size, MB",
                   note="the model files", unit=" MB")]
    return bar_chart("Speaker labels: time and size",
                     ["On the test laptop's processor with four threads, in the balanced power mode."],
                     rows, panels, label_x=188)


CHARTS = {
    "models.svg": models_chart,
    "perle-16k.svg": perle_chart,
    "phone-audio.svg": phone_chart,
    "speed.svg": speed_chart,
    "meetings.svg": meetings_chart,
    "cohere-variations.svg": variations_chart,
    "voiceprints.svg": voiceprints_chart,
    "speakers-per-excerpt.svg": speakers_chart,
    "nemotron-excerpts.svg": nemotron_excerpts_chart,
    "speaker-models.svg": speaker_models_chart,
    "speaker-der.svg": speaker_der_chart,
    "speaker-cost.svg": speaker_cost_chart,
}


def build():
    """Every chart, as {file name: SVG text}."""
    src = Sources()
    return {name: draw(src) for name, draw in CHARTS.items()}


def main():
    charts = build()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        if old.name not in charts:
            old.unlink()
    for name, content in charts.items():
        (OUT / name).write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {len(charts)} charts to {OUT}")


if __name__ == "__main__":
    main()
