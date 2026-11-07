"""Transcript helpers: lines from word lists, speaker names, and the export formats.

Every export takes the job, its lines and optionally the job's details (report.details): plain text
starts with a short header, WebVTT with a NOTE, Markdown ends with a Details section, JSON has them
whole. SRT has no place for comments, so it never carries them."""
import json
import re

from . import report

MAX_GAP = 1.5   # a pause longer than this (seconds) starts a new line
MAX_LINE = 30   # longest line, seconds
# In the text, subtitle and Markdown exports a mostly Arabic line starts with a right-to-left mark, so
# players and editors lay it out right to left even when it opens with an English word ("order ال
# project ..."). Mostly Arabic is decided as mixDir() in app.js does: at least 30% of the words that
# have letters contain an Arabic letter (الـdata counts as Arabic).
AR_LETTER = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
LATIN_LETTER = re.compile(r"[A-Za-z\u00C0-\u024F]")
RLM = "\u200f"  # RIGHT-TO-LEFT MARK


def relabel(raw_ids):
    """Map raw speaker ids ("speaker_0", "S2", ...) to "1", "2", ... in order of first appearance."""
    mapping = {}
    for raw in raw_ids:
        if raw is not None and raw not in mapping:
            mapping[raw] = str(len(mapping) + 1)
    return mapping


def lines_from_words(words, max_gap=MAX_GAP, max_line=MAX_LINE):
    """Group words into lines. words: dicts with start, end, text, speaker and glue (True when the
    token attaches to the previous one without a space, e.g. punctuation)."""
    lines = []
    for w in words:
        last = lines[-1] if lines else None
        same = last and last["speaker"] == w["speaker"] and w["start"] - last["end"] < max_gap \
            and w["end"] - last["start"] < max_line
        if last and (w["glue"] or same):
            last["text"] += w["text"] if w["glue"] else " " + w["text"]
            last["end"] = round(max(last["end"], w["end"]), 2)
        elif not w["glue"]:
            lines.append({"start": round(w["start"], 2), "end": round(w["end"], 2),
                          "speaker": w["speaker"], "text": w["text"]})
    return [x for x in lines if x["text"].strip()]


def from_transcribe_py(lines):
    """transcribe.py lines: speaker is 1, 2, ... or None."""
    return [{"start": float(x["start"]), "end": float(x["end"]),
             "speaker": None if x.get("speaker") is None else str(x["speaker"]), "text": x["text"].strip()}
            for x in lines if x.get("text", "").strip()]


def speaker_ids(lines):
    return sorted({x["speaker"] for x in lines if x["speaker"] is not None}, key=lambda s: (len(s), s))


def speaker_name(job, sid):
    if sid is None:
        return ""
    return (job.get("speaker_names") or {}).get(sid) or f"Speaker {sid}"


def clock(t):
    t = int(t)
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _stamp(t, sep):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def mostly_arabic(text):
    arabic = latin = 0
    for word in str(text or "").split():
        if AR_LETTER.search(word):
            arabic += 1
        elif LATIN_LETTER.search(word):
            latin += 1
    return arabic > 0 and arabic >= 0.3 * (arabic + latin)


def rtl_mark(text):
    """The right-to-left mark to put in front of a mostly Arabic line, else ""."""
    return RLM if mostly_arabic(text) else ""


def to_txt(job, lines, details=None):
    out = [*report.summary(details, job.get("title")), ""] if details else []
    for x in lines:
        who = speaker_name(job, x["speaker"])
        out.append(f"{rtl_mark(x['text'])}[{clock(x['start'])}] {who + ': ' if who else ''}{x['text']}")
    return "\n".join(out) + "\n"


def to_srt(job, lines, details=None):
    out = []
    for i, x in enumerate(lines, 1):
        who = speaker_name(job, x["speaker"])
        out.append(f"{i}\n{_stamp(x['start'], ',')} --> {_stamp(x['end'], ',')}\n"
                   f"{rtl_mark(x['text'])}{who + ': ' if who else ''}{x['text']}\n")
    return "\n".join(out)


def to_vtt(job, lines, details=None):
    out = ["WEBVTT", ""]
    if details:  # players skip a NOTE; it ends at the blank line and may not contain "-->"
        out += ["NOTE", *(x.replace("-->", "->") for x in report.summary(details, job.get("title"))), ""]
    for x in lines:
        who = speaker_name(job, x["speaker"])
        out.append(f"{_stamp(x['start'], '.')} --> {_stamp(x['end'], '.')}\n"
                   f"{rtl_mark(x['text'])}{f'<v {who}>' if who else ''}{x['text']}\n")
    return "\n".join(out)


def to_md(job, lines, details=None):
    out = [f"# {job.get('title') or 'Transcript'}", "",
           f"*{job.get('model_title', job.get('model', ''))} · {clock(job.get('audio_s') or 0)} · "
           f"{(job.get('created') or '')[:16].replace('T', ' ')}*", ""]
    for x in lines:
        # the mark goes on both lines of an entry: a Markdown viewer takes the direction from the start of the
        # paragraph, a text editor from the start of each line
        who, m = speaker_name(job, x["speaker"]), rtl_mark(x["text"])
        out.append(f"{m}**{who}** `{clock(x['start'])}`  \n{m}{x['text']}\n" if who
                   else f"{m}`{clock(x['start'])}` {x['text']}\n")
    if details:
        out.append(report.markdown(details))
    return "\n".join(out)


def to_json(job, lines, details=None):
    return json.dumps({
        "title": job.get("title"), "created": job.get("created"), "model": job.get("model"),
        "audio_s": job.get("audio_s"), "language": (job.get("options") or {}).get("language"),
        "speakers": {sid: speaker_name(job, sid) for sid in speaker_ids(lines)},
        **({"details": details} if details else {}),
        "lines": [{**x, "speaker_name": speaker_name(job, x["speaker"]) or None} for x in lines],
    }, ensure_ascii=False, indent=1)


EXPORTS = {
    "txt": (to_txt, "text/plain; charset=utf-8"),
    "srt": (to_srt, "application/x-subrip; charset=utf-8"),
    "vtt": (to_vtt, "text/vtt; charset=utf-8"),
    "md": (to_md, "text/markdown; charset=utf-8"),
    "json": (to_json, "application/json; charset=utf-8"),
}
