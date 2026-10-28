"""Transcripts of one recording: grouping them, comparing them side by side, and combining them.

A recording is often transcribed with several models. "Run again with" makes a new job on the same
audio (rerun_of, audio.flac shared by hardlink), and the same file can also be added again. Jobs are in
one group when rerun_of links them, or when their audio has the same fingerprint: the SHA-256 of the decoded samples, stored in job.json when the audio is prepared and
worked out in the background for jobs made before that.

Comparing lines the transcripts up by time. Rows are cut only where no line of any transcript is
running, give or take a little at the ends of lines (models place boundaries differently), so each
line is in exactly one row: the one its middle falls in. Words that differ are marked after a light
normalization, so punctuation, case, diacritics and common spelling variants don't count.

Combining keeps the base transcript's lines everywhere except in the stretches picked from another one
(a row or a time range; where picks overlap, the later one wins). A line is kept or dropped whole,
depending on where its middle is. The other transcripts' speakers are mapped onto the base's by how
long they talk at the same time, and the base's speaker names carry over. The result is saved as a new
version of the base transcript (app/history.py), so it is reviewed as a diff first and can be restored
from, or undone in, its History; version 0 stays the model's output. The version records where each
stretch came from: the transcript, its label and the version it was at.

A recording works like a repository: one of its transcripts can be its main one ("main": true in job.json,
POST /api/jobs/<id>/main), and the others are its branches: the model transcripts, and branches made from a
version of any of them (Runner.branch in app/jobs.py). Each is named by its label: a branch's name, else the
model's title, numbered when two have the same one. Without a main transcript a recording works as before.
"""
import bisect
import contextlib
import difflib
import hashlib
import math
import re
import threading
import unicodedata
from collections import Counter

import soundfile as sf

from . import history
from . import transcript as T
from .engines import partial_lines
from .jobs import ACTIVE, ID_RE

EDGE = 0.75       # seconds at each end of a line that may lie across a row boundary
TINY_ROW = 0.8    # a row shorter than this (seconds) that only some transcripts have joins a neighbour...
JOIN_GAP = 1.0    # ...when that neighbour is closer than this
MAX_COMPARE = 3
SPEAKER_ID = re.compile(r"\d{1,3}")


class Invalid(Exception):
    """A compare or combine request that can't be done; status is the HTTP status to answer with."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---- fingerprints and groups ---------------------------------------------------------------------

def fingerprint(path):
    """SHA-256 of the decoded samples of an audio.flac: the same recording converted twice gives the
    same samples, even where the FLAC files differ (another encoder version, say)."""
    h = hashlib.sha256()
    with sf.SoundFile(str(path)) as f:
        h.update(f"{f.samplerate}/{f.channels}:".encode())
        for block in f.blocks(blocksize=1 << 18, dtype="int16"):
            h.update(block.tobytes())
    return h.hexdigest()


def try_fingerprint(path):
    """fingerprint(), or None if the file can't be read (grouping is a convenience, never a failure)."""
    try:
        return fingerprint(path)
    except Exception:
        return None


_state_lock = threading.Lock()
_work_lock = threading.Lock()
_filling = set()     # data folders with fill_fingerprints running in the background
_unreadable = set()  # (data folder, job id) whose audio couldn't be read: not tried again


def _needs_fingerprint(store, job):
    return (not job.get("fingerprint") and job["status"] not in ACTIVE
            and (store.root, job["id"]) not in _unreadable and (store.dir(job["id"]) / "audio.flac").exists())


def fill_fingerprints(store):
    """Work out the fingerprint of jobs prepared before fingerprints were stored. A file that re-runs
    share is read once; jobs still being prepared get theirs from the prepare step."""
    with _work_lock:
        seen = {}  # (device, inode) -> fingerprint
        for job in store.list():
            if not _needs_fingerprint(store, job):
                continue
            audio = store.dir(job["id"]) / "audio.flac"
            try:
                st = audio.stat()
                key = (st.st_dev, st.st_ino) if st.st_ino else None  # some Windows drives report no inode
                fp = seen.get(key) or fingerprint(audio)
                store.update(job["id"], fingerprint=fp)
            except Exception:  # unreadable, or deleted meanwhile: the job stays on its own
                _unreadable.add((store.root, job["id"]))
                continue
            if key:
                seen[key] = fp


def fill_later(store, jobs):
    """Run fill_fingerprints in the background if a job needs it, so the list isn't held up."""
    if not any(_needs_fingerprint(store, j) for j in jobs):
        return
    with _state_lock:
        if store.root in _filling:
            return
        _filling.add(store.root)

    def run():
        try:
            fill_fingerprints(store)
        finally:
            with _state_lock:
                _filling.discard(store.root)
    threading.Thread(target=run, daemon=True, name="sedjem-fingerprints").start()


def group_ids(jobs):
    """{job id: group id}. Jobs linked by rerun_of, or with the same audio fingerprint, are in one
    group, which is named after its oldest job."""
    root = {j["id"]: j["id"] for j in jobs}

    def find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    def join(a, b):
        if a in root and b in root:
            a, b = find(a), find(b)
            root[max(a, b)] = min(a, b)  # ids start with the time the job was made: the oldest wins

    first = {}  # fingerprint -> the first job seen with it
    for j in jobs:
        join(j["id"], j.get("rerun_of"))
        if j.get("fingerprint"):
            join(j["id"], first.setdefault(j["fingerprint"], j["id"]))
    return {jid: find(jid) for jid in root}


def group_of(store, jid):
    """(group id, the group's jobs, oldest first). Older jobs' fingerprints are worked out first."""
    fill_fingerprints(store)
    jobs = store.list()
    ids = group_ids(jobs)
    if jid not in ids:
        raise Invalid(404, "no such transcription")
    return ids[jid], sorted((j for j in jobs if ids[j["id"]] == ids[jid]), key=lambda j: j["id"])


def lines_of(store, jid):
    data = store.transcript(jid)
    return data["lines"] if data else partial_lines(store.dir(jid))


# ---- main and branches -----------------------------------------------------------------------------

def main_of(jobs):
    """The main transcript among the jobs of one recording, or None: the finished one marked main. Marking one
    unmarks the others of its group, so there is one; when two groups that each had one become one (the same
    audio found later), the newest of them."""
    marked = [j for j in jobs if j.get("main") is True and j.get("status") == "done"]
    return max(marked, key=lambda j: j["id"]) if marked else None


def mains(jobs, groups):
    """{group id: its main transcript's id} for the groups that have one. groups: group_ids(jobs)."""
    by = {}
    for j in jobs:
        by.setdefault(groups[j["id"]], []).append(j)
    return {gid: main["id"] for gid, members in by.items() if (main := main_of(members))}


def label(job):
    """A transcript's name in its recording: a branch's name, else its model's title."""
    branch = job.get("branch") if isinstance(job.get("branch"), dict) else {}
    return str(branch.get("name") or job.get("model_title") or job.get("model") or "")


def labels(jobs):
    """{job id: label} for the transcripts of one recording, given oldest first: numbered when two or more have the
    same one ("Cohere Transcribe Arabic 2"), as verLabels in app/static/compare.js."""
    count, seen, out = Counter(label(j) for j in jobs), Counter(), {}
    for j in jobs:
        name = label(j)
        seen[name] += 1
        out[j["id"]] = f"{name} {seen[name]}" if count[name] > 1 else name
    return out


def default_base(ids, main):
    """The base when none is given: the recording's main transcript when it is one of ids (text is brought into it),
    else the first."""
    return main["id"] if main is not None and main["id"] in ids else ids[0]


def main_first(jobs):
    """The jobs of one recording (oldest first) with its main transcript first, if it has one."""
    main = main_of(jobs)
    return ([main] if main else []) + [j for j in jobs if j is not main]


# ---- words that differ ---------------------------------------------------------------------------

DIACRITICS = re.compile(r"[ً-ْٰـ]")  # harakat, superscript alef, tatweel
SPELLING = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه",
                          **{chr(0x660 + i): str(i) for i in range(10)},
                          **{chr(0x6F0 + i): str(i) for i in range(10)}})
SCRIPT_CHANGE = re.compile(r"(?<=[؀-ۿ])(?=[A-Za-z])|(?<=[A-Za-z])(?=[؀-ۿ])")


def normalize(word):
    """The comparable parts of a word, with the benchmark's rules (bench/score.py): no diacritics,
    tatweel, punctuation or case; one spelling for alef, final ya and ta marbuta; Arabic-Indic digits
    as 0-9; an Arabic prefix split off an English word (الـdata: ال, data)."""
    word = DIACRITICS.sub("", unicodedata.normalize("NFKC", word)).translate(SPELLING)
    word = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in word)
    return SCRIPT_CHANGE.sub(" ", word).lower().split()


def words(text):
    """A line's words as shown (split at spaces), each with its normalized parts."""
    return [(w, normalize(w)) for w in str(text or "").split()]


def differences(cells):
    """For each word of each cell: 0 if every other cell has it in the same place, 1 if some do, 2 if
    none do. cells: one list of words() per transcript. A word is as different as its most different
    part; a word with no letters or digits (a dash, say) never counts."""
    parts = [[(n, p) for n, (_, ps) in enumerate(ws) for p in ps] for ws in cells]
    seqs = [[p for _, p in ps] for ps in parts]
    matched = [[0] * len(s) for s in seqs]  # how many other cells match each part
    for a in range(len(cells)):
        for b in range(a + 1, len(cells)):
            for block in difflib.SequenceMatcher(None, seqs[a], seqs[b], autojunk=False).get_matching_blocks():
                for d in range(block.size):
                    matched[a][block.a + d] += 1
                    matched[b][block.b + d] += 1
    others = len(cells) - 1
    out = []
    for ws, ps, m in zip(cells, parts, matched):
        codes = [0] * len(ws)
        for (n, _), count in zip(ps, m):
            codes[n] = max(codes[n], 0 if count == others else 1 if count else 2)
        out.append(codes)
    return out


def spans(ws, codes):
    """Runs of words with the same mark: [[text, code], ...]."""
    out = []
    for (w, _), code in zip(ws, codes):
        if out and out[-1][1] == code:
            out[-1][0] += " " + w
        else:
            out.append([w, code])
    return out


# ---- lining up by time ---------------------------------------------------------------------------

def _runs(items, edge):
    """Where no row boundary may go: the inside of each line, less `edge` seconds at each end (less a
    quarter of a short line), merged into sorted runs [[a, b], ...]."""
    cores = sorted((s + min(edge, (e - s) / 4), e - min(edge, (e - s) / 4)) for s, e, _, _ in items if e > s)
    runs = []
    for a, b in cores:
        if runs and a < runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], b)
        else:
            runs.append([a, b])
    return runs


def _coverage(items):
    """How many lines are running when: [(t0, t1, count)], in order."""
    events = sorted([(s, 1) for s, _, _, _ in items] + [(e, -1) for _, e, _, _ in items])
    segs, count, prev = [], 0, None
    for t, d in events:
        if prev is not None and t > prev:
            segs.append((prev, t, count))
        count, prev = count + d, t
    return segs


def _quietest(segs, starts, a, b):
    """The point of [a, b] where the fewest lines are running (the middle of the longest such piece)."""
    best = None
    for t0, t1, count in segs[max(0, bisect.bisect_right(starts, a) - 1):]:
        if t0 >= b:
            break
        lo, hi = max(a, t0), min(b, t1)
        if hi > lo and (best is None or (count, lo - hi) < best[:2]):
            best = (count, lo - hi, (lo + hi) / 2)
    return best[2] if best else (a + b) / 2


def align(tracks, edge=EDGE):
    """Rows of lines from the same stretch of audio. tracks: the lines of each transcript.

    Returns [{"from", "to", "start", "end", "lines": [[line index, ...] for each track]}]. A line is in
    the row its middle falls in, [from, to); rows are cut where no line runs, or failing that where the
    fewest do, allowing `edge` seconds at the ends of lines. start and end are the span of the lines."""
    items = [(float(x["start"]), max(float(x["start"]), float(x["end"])), k, i)
             for k, lines in enumerate(tracks) for i, x in enumerate(lines)]
    if not items:
        return []
    runs, segs = _runs(items, edge), _coverage(items)
    starts = [s[0] for s in segs]
    cuts = sorted(round(_quietest(segs, starts, runs[r][1], runs[r + 1][0]), 2) for r in range(len(runs) - 1))
    last = round(max(e for _, e, _, _ in items) + 1, 2)
    bounds = [0.0, *cuts, last]
    rows = [{"from": bounds[r], "to": bounds[r + 1], "lines": [[] for _ in tracks]} for r in range(len(cuts) + 1)]
    for s, e, k, i in items:
        rows[bisect.bisect_right(cuts, (s + e) / 2)]["lines"][k].append(i)
    rows = [r for r in rows if any(r["lines"])]
    for r in rows:
        _span(r, tracks)
    return _join_tiny(rows, tracks)


def _span(row, tracks):
    for k in range(len(tracks)):
        row["lines"][k].sort()
    lines = [tracks[k][i] for k, ids in enumerate(row["lines"]) for i in ids]
    row["start"] = min(float(x["start"]) for x in lines)
    row["end"] = max(float(x["end"]) for x in lines)


def _join_tiny(rows, tracks):
    """A very short row that only some transcripts have (a word one model heard, say) joins the nearer
    neighbour when that is close, so it shows as a difference there instead of a row of its own."""
    i = 0
    while i < len(rows) and len(rows) > 1:
        r = rows[i]
        if r["end"] - r["start"] < TINY_ROW and not all(r["lines"]):
            before = r["start"] - rows[i - 1]["end"] if i > 0 else math.inf
            after = rows[i + 1]["start"] - r["end"] if i + 1 < len(rows) else math.inf
            if min(before, after) < JOIN_GAP:
                j = i - 1 if before <= after else i + 1
                into = rows[j]
                into["from"], into["to"] = min(into["from"], r["from"]), max(into["to"], r["to"])
                into["lines"] = [a + b for a, b in zip(into["lines"], r["lines"])]
                _span(into, tracks)
                del rows[i]
                i = max(0, i - 1)
                continue
        i += 1
    return rows


# ---- speakers ------------------------------------------------------------------------------------

def talk_overlap(src, base):
    """{source speaker: {base speaker: seconds both are talking}}."""
    base = sorted((x for x in base if x["speaker"] is not None), key=lambda x: x["start"])
    starts = [x["start"] for x in base]
    longest = max((x["end"] - x["start"] for x in base), default=0)
    out = {}
    for x in src:
        if x["speaker"] is None:
            continue
        mine = out.setdefault(x["speaker"], {})
        j = bisect.bisect_left(starts, x["end"]) - 1
        while j >= 0 and base[j]["start"] >= x["start"] - longest:
            o = min(x["end"], base[j]["end"]) - max(x["start"], base[j]["start"])
            if o > 0:
                mine[base[j]["speaker"]] = mine.get(base[j]["speaker"], 0) + o
            j -= 1
    return out


def map_speakers(src, base):
    """{source speaker: base speaker}, by talk time together. First one to one, largest overlaps first
    (a pair counts only if it has at least half the source speaker's best overlap, so a few seconds of
    timing noise don't decide); the rest go to the speaker they overlap most, so a person that the
    source split in two maps to one base speaker. Speakers that never overlap one get new numbers."""
    overlap = talk_overlap(src, base)
    best = {s: max(o.values(), default=0) for s, o in overlap.items()}
    pairs = sorted(((sec, s, b) for s, o in overlap.items() for b, sec in o.items() if sec >= best[s] / 2),
                   key=lambda p: (-p[0], p[1], p[2]))
    out, taken = {}, set()
    for _, s, b in pairs:
        if s not in out and b not in taken:
            out[s] = b
            taken.add(b)
    new = max((int(b) for b in T.speaker_ids(base)), default=0) + 1
    for s in dict.fromkeys(x["speaker"] for x in src if x["speaker"] is not None):
        if s in out:
            continue
        if best.get(s, 0) > 0:
            out[s] = max(overlap[s].items(), key=lambda kv: (kv[1], -int(kv[0])))[0]
        else:
            out[s], new = str(new), new + 1
    return out


# ---- combining -----------------------------------------------------------------------------------

def timeline(picks, base):
    """The stretches kept from transcripts other than the base: [(start, end, job id)], in order, from
    the picks (dicts with start, end and from) applied in turn, a later pick winning where they overlap.
    Neighbouring stretches of one transcript are joined."""
    segs = []
    for p in picks:
        s, e, rest = p["start"], p["end"], []
        for a, b, o in segs:  # what is left of the earlier stretches around this pick
            if a < s:
                rest.append((a, min(b, s), o))
            if b > e:
                rest.append((max(a, e), b, o))
        segs = sorted(rest + [(s, e, p["from"])])
    out = []
    for a, b, o in segs:
        if out and out[-1][2] == o and out[-1][1] == a:
            out[-1] = (out[-1][0], b, o)
        else:
            out.append((a, b, o))
    return [x for x in out if x[2] != base]


def combine(tracks, base, picks, mapping):
    """The combined lines, in order. tracks: {job id: lines}; a line is kept when its middle is in a
    stretch kept from its own transcript (the base's wherever nothing was picked). mapping: {job id:
    {speaker: base speaker}} for the other transcripts."""
    segs = timeline(picks, base)
    starts = [s for s, _, _ in segs]

    def owner(t):
        i = bisect.bisect_right(starts, t) - 1
        return segs[i][2] if i >= 0 and t < segs[i][1] else base

    out = []
    for jid, lines in tracks.items():
        speakers = mapping.get(jid) or {}
        for x in lines:
            if owner((x["start"] + x["end"]) / 2) == jid:
                who = x["speaker"] if jid == base or x["speaker"] is None else speakers.get(x["speaker"], x["speaker"])
                out.append({"start": x["start"], "end": x["end"], "speaker": who, "text": x["text"]})
    return sorted(out, key=lambda x: (x["start"], x["end"]))


# ---- the API: GET /api/compare, POST /api/combine (and /api/combine/preview) ------------------------------------------------

def comparable(store, ids):
    """The jobs to compare or combine, in the order given: 2 or 3 finished transcripts of one recording."""
    return _comparable(store, ids)[0]


def _comparable(store, ids):
    """(comparable(), the recording's jobs oldest first)."""
    if not 2 <= len(ids) <= MAX_COMPARE or len(set(ids)) != len(ids):
        raise Invalid(400, "choose 2 or 3 different transcripts")
    for jid in ids:
        if store.get(jid) is None:
            raise Invalid(404, "no such transcription")
    _, group = group_of(store, ids[0])
    by_id = {j["id"]: j for j in group}
    if any(jid not in by_id for jid in ids):
        raise Invalid(400, "these are transcripts of different recordings")
    if any(by_id[jid]["status"] in ACTIVE for jid in ids):
        raise Invalid(409, "wait until the transcription has finished")
    return [by_id[jid] for jid in ids], group


def job_info(job, lines, name=None, main=False):
    talk = {}
    for x in lines:
        if x["speaker"] is not None:
            talk[x["speaker"]] = talk.get(x["speaker"], 0) + max(0.0, x["end"] - x["start"])
    return {**{k: job.get(k) for k in ("id", "title", "model", "model_title", "kind", "status", "created", "branch")},
            "label": name or label(job), "main": main, "speaker_names": job.get("speaker_names") or {},
            "speakers": [{"id": s, "talk": round(talk[s], 1)} for s in T.speaker_ids(lines)]}


def view(store, ids, base=None):
    """What the compare view shows: the transcripts, the rows lined up by time with the words that
    differ marked, and how the other transcripts' speakers map onto the base's."""
    jobs, group = _comparable(store, ids)
    names, main = labels(group), main_of(group)
    base = base or default_base(ids, main)
    if base not in ids:
        raise Invalid(400, "the base must be one of the transcripts compared")
    tracks = [lines_of(store, jid) for jid in ids]
    base_lines = tracks[ids.index(base)]
    rows = []
    for r in align(tracks):
        cells = [[tracks[k][i] for i in r["lines"][k]] for k in range(len(ids))]
        ws = [[words(x["text"]) for x in cell] for cell in cells]
        codes = differences([[w for line in cell for w in line] for cell in ws])
        out = []
        for k, cell in enumerate(cells):
            at, shown = 0, []
            for i, x, lw in zip(r["lines"][k], cell, ws[k]):
                shown.append({"i": i, "start": x["start"], "end": x["end"], "speaker": x["speaker"],
                              "text": x["text"], "spans": spans(lw, codes[k][at:at + len(lw)])})
                at += len(lw)
            out.append(shown)
        same = len({tuple(p for line in cell for _, ps in line for p in ps) for cell in ws}) == 1
        rows.append({"from": r["from"], "to": r["to"], "start": r["start"], "end": r["end"], "same": same,
                     "cells": out})
    others = [(jid, lines) for jid, lines in zip(ids, tracks) if jid != base]
    return {
        "ids": ids, "base": base, "audio_s": jobs[ids.index(base)].get("audio_s"),
        "jobs": [job_info(j, lines, names.get(j["id"]), main is not None and j["id"] == main["id"])
                 for j, lines in zip(jobs, tracks)],
        "mapping": {jid: map_speakers(lines, base_lines) for jid, lines in others},
        "overlap": {jid: {s: {b: round(sec, 1) for b, sec in o.items()} for s, o in talk_overlap(lines, base_lines).items()}
                    for jid, lines in others},
        "rows": rows,
    }


def clean_picks(picks, ids):
    if not isinstance(picks, list) or len(picks) > 20000:
        raise Invalid(400, "picks must be a list")
    out = []
    for p in picks:
        try:
            start, end, src = float(p["start"]), float(p["end"]), str(p["from"])
        except (KeyError, TypeError, ValueError):
            raise Invalid(400, "each pick needs a start, an end and the transcript it is from")
        if not (math.isfinite(start) and math.isfinite(end)) or end <= start:
            raise Invalid(400, "a pick must end after it starts")
        if src not in ids:
            raise Invalid(400, "a pick is from a transcript that isn't being combined")
        out.append({"start": start, "end": end, "from": src})
    return out


def clean_speakers(given, ids, base):
    """The user's changes to the speaker mapping: {job id: {speaker: base speaker}}."""
    if not isinstance(given or {}, dict):
        raise Invalid(400, "speakers must map each transcript's speakers to the base's")
    out = {}
    for jid, m in (given or {}).items():
        if jid not in ids or jid == base:
            continue
        if not isinstance(m, dict) or not all(SPEAKER_ID.fullmatch(str(s)) and SPEAKER_ID.fullmatch(str(t))
                                              for s, t in m.items()):
            raise Invalid(400, "speaker ids are numbers")
        out[jid] = {str(s): str(t) for s, t in m.items()}
    return out


def combination(store, body, cfg=None):
    """What saving a combination would change on the base transcript (POST /api/combine): body has
    ids, base, picks [{start, end, from}] and speakers (changes to the computed mapping). Returns
    (base job id, the change for history.save, the summary, what the version records)."""
    if not isinstance(body, dict):
        raise Invalid(400, "expected a JSON object")
    ids = _ids(body)
    jobs, group = _comparable(store, ids)
    by_id = {j["id"]: j for j in jobs}
    base = str(body.get("base") or default_base(ids, main_of(group)))
    if base not in by_id:
        raise Invalid(400, "the base must be one of the transcripts combined")
    picks = clean_picks(body.get("picks", []), ids)
    changes = clean_speakers(body.get("speakers"), ids, base)
    versions = {jid: history.current(store, jid, cfg) for jid in ids}  # the version each one is at (its history in step)
    tracks = {jid: lines_of(store, jid) for jid in ids}
    mapping = {jid: {**map_speakers(tracks[jid], tracks[base]), **changes.get(jid, {})} for jid in ids if jid != base}
    lines = combine(tracks, base, picks, mapping)
    ranges = timeline(picks, base)
    if not ranges:
        raise Invalid(400, "pick some text from another transcript first")

    names = dict(by_id[base].get("speaker_names") or {})  # the base's names carry over
    used = {x["speaker"] for x in lines}
    for jid, m in mapping.items():  # a speaker the base doesn't have keeps its own name
        own = by_id[jid].get("speaker_names") or {}
        for s, t in m.items():
            if t in used and t not in names and own.get(s):
                names[t] = own[s]
    sources = [jid for jid in ids if jid != base and any(o == jid for _, _, o in ranges)]
    audio_s = by_id[base].get("audio_s")
    title = lambda jid: by_id[jid].get("model_title") or by_id[jid].get("model")  # noqa: E731
    named = labels(group)
    parts = [f"{named[jid]} (version {versions[jid]}) for "
             + ", ".join(f"{T.clock(a)}–{T.clock(min(b, audio_s or b))}" for a, b, o in ranges if o == jid)
             for jid in sources]
    summary = "With the text of " + "; ".join(parts)
    record = {"sources": [{"id": jid, "model_title": title(jid), "label": named[jid], "version": versions[jid]}
                          for jid in sources],
              "ranges": [{"start": round(a, 2), "end": round(b, 2), "from": o, "version": versions[o]}
                         for a, b, o in ranges],
              "speakers": {jid: mapping[jid] for jid in sources}}
    return base, {"lines": lines, "speaker_names": names}, summary, record


def _ids(body):
    return [str(x) for x in body.get("ids") or []] if isinstance(body, dict) and isinstance(body.get("ids"), list) else []


def preview_combined(store, body, cfg=None):
    """The review before saving: what the new version of the base would change (history.preview)."""
    base, change, summary, _ = combination(store, body, cfg)
    found = history.preview(store, base, change, cfg)
    if found is None:
        raise Invalid(409, "wait until the transcription has finished")
    return {**found, "base": base, "summary": f"{summary}: {found['summary'][:1].lower()}{found['summary'][1:]}"}


def save_combined(store, body, cfg=None):
    """Save a combination as a new version of the base transcript, with an optional message (body
    "message"). Version 0 stays the model's output, and the version can be reviewed and restored in
    History like any other. Returns (base job id, the version's entry)."""
    ids = _ids(body)
    with store.lock, contextlib.ExitStack() as held:  # versions are numbered in the order they are saved
        if 2 <= len(ids) <= MAX_COMPARE:  # every transcript read stays as it is meanwhile (the MCP server saves too)
            for jid in sorted({x for x in ids if ID_RE.match(x)}):
                held.enter_context(history.locked(store, jid))
        base, change, summary, record = combination(store, body, cfg)
        found = history.preview(store, base, change, cfg)
        if found is None:
            raise Invalid(409, "wait until the transcription has finished")
        entry = history.save(store, base, change, "combine", (body or {}).get("message"),
                             f"{summary}: {found['summary'][:1].lower()}{found['summary'][1:]}", cfg,
                             combined=record)
    if entry is None:
        raise Invalid(409, "the transcript already has this text")
    return base, entry
