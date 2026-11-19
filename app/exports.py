"""Exporting many transcripts at once (#9): the transcripts chosen in the Export dialog, in one format, as a zip or
as files for a folder, or as one combined file with them all in the order chosen.

Every file is named after its transcript's title, as a single export is (app/server.py, slug). A course's lectures
go in a folder named after the course, numbered in playlist order; a name used twice gets -2, -3, and a name Windows
reserves (CON, NUL, AUX…) gets _, as course folders do (app/courses.py). The text of each file is what the
transcript's own Export gives: its current version, with the details.
"""
import io
import zipfile
from pathlib import Path

from . import report
from . import transcript as T
from .courses import RESERVED

COMBINED = ("txt", "md", "json")  # subtitles have one timeline for each file, so they aren't put in one
RULE = "\n" + "-" * 60 + "\n\n"  # between the transcripts of a combined text file


def chosen(store, ids):
    """[(job, transcript)] of the transcriptions with these ids that have a transcript, in the order given, each once.
    KeyError with the id of one that doesn't exist."""
    out = []
    for jid in dict.fromkeys(ids):
        job = store.get(jid)
        if job is None:
            raise KeyError(jid)
        data = store.transcript(jid)
        if data and isinstance(data.get("lines"), list):
            out.append((job, data))
    return out


def rendered(job, data, fmt):
    lines, edited = data["lines"], bool(data.get("edited"))
    return T.EXPORTS[fmt][0](job, lines, report.details(job, lines, edited))


def safe(name):
    """A name Windows reserves, with _ after it."""
    return f"{name}_" if RESERVED.match(name) else name


def files(jobs, fmt, slug):
    """[(path, text)]: one file for each transcript, its path relative to the zip or the folder, with / between
    folders. Lecture numbers have as many digits as the course's count, at least two, so they sort in order."""
    out, used = [], set()
    for job, data in jobs:
        course, name = job.get("course") or {}, safe(slug(job.get("title")))
        if course.get("title") and course.get("index") is not None:
            width = max(2, len(str(course.get("count") or "")))
            stem = f"{safe(slug(course['title']))}/{str(course['index']).zfill(width)}-{name}"
        else:
            stem = name
        path, n = f"{stem}.{fmt}", 2
        while path.lower() in used:  # (names that differ only in case are one file on Windows and macOS)
            path, n = f"{stem}-{n}.{fmt}", n + 1
        used.add(path.lower())
        out.append((path, rendered(job, data, fmt)))
    return out


def combined(jobs, fmt):
    """One file with every transcript, in the order given: Markdown with a rule between them, JSON as a list, text
    with a line between them."""
    parts = [rendered(job, data, fmt) for job, data in jobs]
    if fmt == "json":
        return "[\n" + ",\n".join(parts) + "\n]\n"
    if fmt == "md":
        return "\n\n---\n\n".join(p.rstrip("\n") for p in parts) + "\n"
    return RULE.join(parts)


def zipped(found):
    """The files in a zip, deflated."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in found:
            z.writestr(path, text.encode("utf-8"))
    return buf.getvalue()


def name(jobs, slug):
    """The name of the zip or the combined file, without its extension: the course's, when every transcript is a
    lecture of the same course, else sedjem-transcripts."""
    courses = {(job.get("course") or {}).get("id") for job, _ in jobs}
    title = (jobs[0][0].get("course") or {}).get("title") if len(courses) == 1 and None not in courses else None
    return safe(slug(title)) if title else "sedjem-transcripts"


def write(folder, found):
    """Write the files into folder (the desktop window's Into a folder), each inside it; returns how many."""
    folder = Path(folder).resolve()
    for path, text in found:
        dest = (folder / path).resolve()
        if folder not in dest.parents:
            raise ValueError(f"not inside the folder: {path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return len(found)
