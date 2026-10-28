"""The courses folder: the transcripts of YouTube videos as files, to read or to paste into a chat assistant.

  <courses>/<playlist title>/NN - <video title>.md   one per finished lecture: its title, link and transcript
  <courses>/<playlist title>/NN - <video title>.srt  the same as subtitles
  <courses>/<playlist title>/course.md               all finished lectures in playlist order, as plain text
  <courses>/Videos/<video title> [<video id>].md     a video added on its own (and its .srt)

<courses> is [storage] courses in the config, next to app_data and models in the data folder. The files are
written when a transcription finishes and again whenever its transcript, title or speaker names change, or
another transcription becomes the main one; a lecture whose transcriptions were all deleted loses its files.
When a video was transcribed more than once, the files follow its main transcript when one is chosen
(app/compare.py), else the transcription that finished last, leaving out branches that aren't main (unless
nothing else is left). Only files of these names are ever written or removed.
"""
import re
import threading
import unicodedata
from pathlib import Path

from . import transcript as T

VIDEOS = "Videos"
RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)  # names Windows doesn't allow
_lock = threading.Lock()


def safe_name(text, limit=100):
    """A title as a file or folder name on every system: no / \\ : * ? " < > | or control characters, no
    dot or space at the end, not a name Windows reserves."""
    text = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in str(text or ""))
    text = " ".join(re.sub(r'[\\/:*?"<>|]+', " ", text).split())[:limit].rstrip(" .")
    if not text:
        return "Untitled"
    return f"{text}_" if RESERVED.match(text) else text


def folder_name(store, course):
    """The folder of a new course: its title, made safe, unless another course already has a folder of that
    name (then the playlist's id is added). Jobs keep it in course["folder"], so it never changes."""
    name = safe_name(course.get("title"))
    for job in store.list():
        other = job.get("course") or {}
        if other.get("id") == course.get("id") and other.get("folder"):
            return other["folder"]
    if name.lower() == VIDEOS.lower() or any((j.get("course") or {}).get("folder", "").lower() == name.lower()
                                             and (j.get("course") or {}).get("id") != course.get("id") for j in store.list()):
        name = safe_name(f"{course.get('title')} ({course.get('id')})")
    return name


def folder_of(cfg, job):
    """Where a YouTube job's files go: a folder directly in the courses folder, whatever job.json says."""
    course = job.get("course") or {}
    folder = cfg.courses / (safe_name(course.get("folder") or course.get("title")) if course else VIDEOS)
    return inside(cfg.courses, folder)


def inside(root, path):
    """path, if it resolves to a file or folder directly in root (no way out through .., a drive or a
    link); else ValueError."""
    root, real = Path(root).resolve(), Path(path).resolve()
    if real.parent != root or real.name in ("", ".", ".."):
        raise ValueError(f"{path} is not in {root}")
    return Path(path)


def number(course):
    """A lecture's number, padded to the width of the last one: 01 ... 12, 001 ... 120."""
    index, count = int(course["index"]), int(course.get("count") or course["index"])
    return str(index).zfill(max(2, len(str(count))))


def lecture_name(job):
    """A lecture's file name without its extension; every part made safe (safe_name)."""
    course = job.get("course")
    title = safe_name(job.get("title"), 90)
    return f"{number(course)} - {title}" if course else f"{title} [{safe_name(job.get('youtube_id'), 64)}]"


def _named(job, lines):
    """Speaker names are shown when there are several speakers, or one has a name."""
    return len(T.speaker_ids(lines)) > 1 or bool(job.get("speaker_names"))


def lecture_md(job, lines):
    """One video: its title, link and where it comes from, then the transcript with times (and speakers)."""
    course = job.get("course")
    out = [f"# {job.get('title') or 'Untitled'}", "", f"Video: {job.get('source_url')}  "]
    if course:
        out.append(f"Course: {course.get('title')}, lecture {course['index']} of {course.get('count')}  ")
    out += [f"Transcript: {job.get('model_title') or job.get('model')}, {T.clock(job.get('audio_s') or 0)}", ""]
    named = _named(job, lines)
    for x in lines:
        who = T.speaker_name(job, x["speaker"]) if named else ""
        out += [f"{T.rtl_mark(x['text'])}[{T.clock(x['start'])}] {who + ': ' if who else ''}{x['text']}", ""]
    return "\n".join(out)


def paragraphs(job, lines, longest=60.0, gap=3.0):
    """The lines as paragraphs of plain text: a new one when the speaker changes, after a pause, or once a
    paragraph is a minute long. Each starts with its time (and its speaker when there are several)."""
    named, out, cur = _named(job, lines), [], None
    for x in lines:
        if cur and x["speaker"] == cur["speaker"] and x["start"] - cur["end"] < gap and x["end"] - cur["start"] <= longest:
            cur["text"] += " " + x["text"]
            cur["end"] = x["end"]
            continue
        cur = {**x}
        out.append(cur)
    texts = []
    for p in out:
        who = T.speaker_name(job, p["speaker"]) if named else ""
        texts.append(f"{T.rtl_mark(p['text'])}[{T.clock(p['start'])}] {who + ': ' if who else ''}{p['text']}")
    return texts


def course_md(course, lectures):
    """All finished lectures of a course in playlist order, for pasting into a chat assistant: a heading per
    lecture, then its text in paragraphs. lectures: [(job, lines)]."""
    count = course.get("count") or len(lectures)
    done = "all" if len(lectures) >= count else f"{len(lectures)} of {count}"
    out = [f"# {course.get('title')}", "", f"Playlist: {course.get('url')}  ",
           f"Transcripts of {done} lectures, in playlist order. Times in brackets are minutes:seconds into each video.", ""]
    for job, lines in lectures:
        c = job["course"]
        out += [f"## Lecture {c['index']}: {job.get('title') or 'Untitled'} ({T.clock(job.get('audio_s') or 0)})", "",
                f"Video: {job.get('source_url')}", ""]
        for p in paragraphs(job, lines):
            out += [p, ""]
    return "\n".join(out)


def _write(path, text):
    """Write a file only when its text changed (a refresh rewrites a course without touching the rest)."""
    if not path.exists() or path.read_text(encoding="utf-8", errors="replace") != text:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def ranked(jobs):
    """A lecture's (or a video's) finished transcriptions, the one to use first: its main transcript, then the others
    that aren't branches, the one that finished last first, then the branches that aren't main."""
    from .compare import main_of  # (it imports app/jobs.py, which imports this module)
    main = main_of(jobs)
    rest = sorted((j for j in jobs if j is not main),
                  key=lambda j: (str(j.get("finished") or ""), str(j.get("created") or ""), j["id"]), reverse=True)
    return ([main] if main else []) + [j for j in rest if not j.get("branch")] + [j for j in rest if j.get("branch")]


def _lectures(store, match):
    """{key: (job, lines)} of the finished jobs that match: for each key (a lecture, or a video on its own) the first
    of ranked() that has a transcript."""
    by_key = {}
    for job in (j for j in store.list() if match(j) and j.get("status") == "done"):
        by_key.setdefault((job.get("course") or {}).get("index") or job.get("youtube_id"), []).append(job)
    found = {}
    for key, jobs in by_key.items():
        for job in ranked(jobs):
            data = store.transcript(job["id"])
            if data and data.get("lines") is not None:
                found[key] = (job, data["lines"])
                break
    return found


def _sync(folder, wanted, pattern):
    """Write the wanted files {name: text} and remove the other files of the same lectures (an old title)."""
    for path in folder.glob(pattern) if folder.exists() else []:
        if path.name not in wanted and path.suffix in (".md", ".srt") and path.name != "course.md" and path.is_file():
            inside(folder, path).unlink(missing_ok=True)
    if wanted:
        folder.mkdir(parents=True, exist_ok=True)
    for name, text in wanted.items():
        _write(inside(folder, folder / name), text)


def refresh(store, cfg, job):
    """Write, rewrite or remove the files of the course (or the lone video) a YouTube job belongs to.
    job: its job.json as it is, or as it was before it was deleted."""
    if not job or not job.get("youtube_id"):
        return
    with _lock:
        course = job.get("course")
        folder = folder_of(cfg, job)
        if course:
            lectures = _lectures(store, lambda j: (j.get("course") or {}).get("id") == course["id"])
            for index in {(j.get("course") or {}).get("index") for j in store.list()
                          if (j.get("course") or {}).get("id") == course["id"]} | {course["index"]}:
                prefix = number({**course, "index": index})
                hit = lectures.get(index)
                wanted = {}
                if hit:
                    name = lecture_name(hit[0])
                    wanted = {f"{name}.md": lecture_md(*hit), f"{name}.srt": T.to_srt(*hit)}
                _sync(folder, wanted, f"{glob_escape(prefix)} - *")
            ordered = [lectures[i] for i in sorted(lectures)]
            if ordered:
                folder.mkdir(parents=True, exist_ok=True)
                _write(inside(folder, folder / "course.md"), course_md({**course, **ordered[-1][0]["course"]}, ordered))
            else:
                (folder / "course.md").unlink(missing_ok=True)
        else:
            vid = job["youtube_id"]
            hit = _lectures(store, lambda j: j.get("youtube_id") == vid and not j.get("course")).get(vid)
            wanted = {}
            if hit:
                name = lecture_name(hit[0])
                wanted = {f"{name}.md": lecture_md(*hit), f"{name}.srt": T.to_srt(*hit)}
            _sync(folder, wanted, f"* [[]{glob_escape(safe_name(vid, 64))}[]].*")
        try:
            folder.rmdir()  # only if nothing is left in it
        except OSError:
            pass


def glob_escape(text):
    return re.sub(r"([*?[])", r"[\1]", text)


def try_refresh(store, cfg, job):
    """refresh(), never raising: the files are a convenience, never a reason for a job or an edit to fail."""
    try:
        refresh(store, cfg, job)
    except Exception:  # noqa: BLE001
        pass
