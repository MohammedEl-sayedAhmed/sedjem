"""Version history of a transcript, kept like version control: the model's output is version 0 and never
changes, and every change saved in the app is a new version (1, 2, ...) with an optional message. The
edits of one session in edit mode are one version, reviewed as a diff before they are saved (preview).
Quick changes outside edit mode (renaming or merging speakers, the title) are a version each, folded
into one when they come close together. Going back to an earlier version saves it again as the newest.

A job folder's history/ holds index.json, one entry per version ({n, time, kind, message, summary,
parent}), and one snapshot per version (v0000.json, v0001.json, ...: title, speaker_names, lines). The
current version is also in transcript.json and job.json as before, so the rest of the app reads those.
A job that finished before the history existed gets one the first time it is opened: version 0 is the
model's output if the folder still has it, version 1 the transcript as it was by then.
"""
import difflib
from datetime import datetime

from . import engines
from . import transcript as T
from .jobs import ACTIVE, now, read_json, write_json

QUICK = ("rename", "merge")  # changes made outside edit mode: renaming, merging speakers, the title
FOLD_SECONDS = 120  # quick changes this close together, without a message, are kept as one version


def clean_message(message):
    return " ".join(str(message or "").split())[:500]


def _folder(store, jid):
    return store.dir(jid) / "history"


def _snapshot(store, jid, n):
    return None if n is None else read_json(_folder(store, jid) / f"v{n:04d}.json")


def _original(store, jid, versions):
    """The model's output (the first version), or None if it wasn't kept."""
    return _snapshot(store, jid, versions[0]["n"]) if versions and versions[0]["kind"] == "model output" else None


def _write(store, jid, versions, entry, state):
    """A version's snapshot first, then the index that lists it (each file is replaced whole)."""
    folder = _folder(store, jid)
    folder.mkdir(exist_ok=True)
    if state is not None:
        write_json(folder / f"v{entry['n']:04d}.json", state)
    write_json(folder / "index.json", {"versions": versions})


def _changed_at(store, jid):
    """When the transcript, or its names and title, were last written: the time of changes made
    before the history saw them."""
    folder = store.dir(jid)
    times = [p.stat().st_mtime for p in (folder / "transcript.json", folder / "job.json") if p.exists()]
    return datetime.fromtimestamp(max(times)).astimezone().isoformat(timespec="seconds") if times else now()


def _age(entry):
    try:
        return (datetime.now().astimezone() - datetime.fromisoformat(entry["time"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return float("inf")


# ---- what a job has now, and what the model wrote ------------------------------------------------------

def live(store, jid, job):
    """The job's title, speaker names and lines as they are now; None while it has no lines to keep."""
    data = store.transcript(jid)
    lines = data["lines"] if data else engines.partial_lines(store.dir(jid))
    if data is None and not lines:
        return None
    return {"title": job.get("title") or "", "speaker_names": dict(job.get("speaker_names") or {}), "lines": lines}


def model_output(store, jid, job, cfg=None):
    """The lines as the model wrote them, or None if they weren't kept: the transcript itself until its
    lines are first edited, then transcribe.py's own output (engine/) or the service's reply (hosted.json)."""
    data = store.transcript(jid)
    if data is not None and not data.get("edited"):
        return data["lines"]
    folder = store.dir(jid)
    if any(not p.name.endswith((".words.json", ".meta.json")) for p in (folder / "engine").glob("*.json")):
        return engines.partial_lines(folder)
    if job.get("captions") and (folder / "captions.vtt").exists():  # YouTube captions made by people
        from .youtube import captions_transcript
        return captions_transcript(folder)
    model = (cfg.models.get(job.get("model")) if cfg else None) or {}
    parse = getattr(engines, f"{model.get('engine') or job.get('model')}_lines", None)  # e.g. elevenlabs_lines
    reply = read_json(folder / "hosted.json") if (folder / "hosted.json").exists() else None
    if reply is None or parse is None:
        return None
    try:
        return parse(reply)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


# ---- differences between two versions ----------------------------------------------------------------

def diff_words(a, b):
    """[["=", "kept words"], ["-", "taken out"], ["+", "put in"], ...] from text a to text b."""
    wa, wb = a.split(), b.split()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(["=", " ".join(wa[i1:i2])])
            continue
        if i2 > i1:
            out.append(["-", " ".join(wa[i1:i2])])
        if j2 > j1:
            out.append(["+", " ".join(wb[j1:j2])])
    return out


def diff_lines(old, new):
    """The lines of new in order, each {"op": "same" | "changed" | "added", "line"}, with the lines of old
    that are gone ({"op": "removed", "line"}) where they were. A changed line also has its "old" version
    and, if its text changed, "words" (see diff_words). Lines are matched by their times, which edits keep."""
    key = lambda x: (x["start"], x["end"])
    sm = difflib.SequenceMatcher(None, [key(x) for x in old], [key(x) for x in new], autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        pairs = min(i2 - i1, j2 - j1) if tag in ("equal", "replace") else 0
        for a, b in zip(old[i1:i1 + pairs], new[j1:j1 + pairs]):
            if key(a) == key(b) and a["text"] == b["text"] and a.get("speaker") == b.get("speaker"):
                rows.append({"op": "same", "line": b})
            else:
                row = {"op": "changed", "line": b, "old": a}
                if a["text"] != b["text"]:
                    row["words"] = diff_words(a["text"], b["text"])
                rows.append(row)
        rows += [{"op": "removed", "line": a} for a in old[i1 + pairs:i2]]
        rows += [{"op": "added", "line": b} for b in new[j1 + pairs:j2]]
    return rows


def renamed(old, new):
    """[(speaker id, name before, name after)] for the speakers shown under another name now."""
    ids = sorted(set(old["speaker_names"]) | set(new["speaker_names"]), key=lambda s: (len(s), s))
    return [(sid, T.speaker_name(old, sid), T.speaker_name(new, sid)) for sid in ids
            if T.speaker_name(old, sid) != T.speaker_name(new, sid)]


def _sentence(parts):
    text = ", ".join(parts) or "no changes"
    return text[:1].upper() + text[1:]


def _line_counts(rows):
    counts = {"changed": 0, "added": 0, "removed": 0}
    for row in rows:
        if row["op"] in counts:
            counts[row["op"]] += 1
    return [f"{k} line{'' if k == 1 else 's'} {op}" for op, k in counts.items() if k]


def _title_changed(title):
    return f"title changed to “{title if len(title) <= 60 else title[:59] + '…'}”"


def describe(old, new):
    """What changed, in a few words: "3 lines changed, Speaker 1 renamed to Mona"."""
    parts = _line_counts(diff_lines(old["lines"], new["lines"]))
    parts += [f"{a} renamed to {b}" for _, a, b in renamed(old, new)]
    if old["title"] != new["title"]:
        parts.append(_title_changed(new["title"]))
    return _sentence(parts)


def quick_summary(old, new, merges):
    """The summary of quick actions (renames, merges, the title) from old to new: the merges as they
    were made ({"from": speaker id, "note": "Speaker 3 merged into Mona (2 lines)"}), then the renames
    and the title change they add up to."""
    gone = {m["from"] for m in merges}
    parts = [m["note"] for m in merges] + [f"{a} renamed to {b}" for sid, a, b in renamed(old, new) if sid not in gone]
    if old["title"] != new["title"]:
        parts.append(_title_changed(new["title"]))
    return _sentence(parts) if parts else describe(old, new)


def compare(old, new):
    """The differences from old to new, as git diff shows them: "stat" counts them ("3 lines changed,
    1 speaker renamed"), then the title and the speaker names that changed, the names on each side,
    and the lines as in diff_lines."""
    rows, names = diff_lines(old["lines"], new["lines"]), renamed(old, new)
    stat = _line_counts(rows) + ([f"{len(names)} speaker{'' if len(names) == 1 else 's'} renamed"] if names else [])
    return {"stat": _sentence(stat + (["title changed"] if old["title"] != new["title"] else [])),
            "title": [old["title"], new["title"]] if old["title"] != new["title"] else None,
            "renamed": [{"id": sid, "from": a, "to": b} for sid, a, b in names],
            "names_before": old["speaker_names"], "names_after": new["speaker_names"], "lines": rows}


# ---- the history of a job ----------------------------------------------------------------------------

def _add(store, jid, versions, old, new, kind, message="", summary=None, time=None, **extra):
    """Save new as the next version; old is the version before it."""
    entry = {"n": versions[-1]["n"] + 1, "time": time or now(), "kind": kind, "message": message,
             "summary": summary or describe(old, new), "parent": versions[-1]["n"], **extra}
    versions.append(entry)
    _write(store, jid, versions, entry, new)
    return entry


def _start(store, jid, job, state, cfg):
    """Version 0, the model's output, and version 1 with any changes made before the history began."""
    lines = model_output(store, jid, job, cfg)
    entry = {"n": 0, "time": job.get("finished") or job.get("created") or now(), "kind": "model output",
             "message": "", "parent": None}
    if lines is None:  # edited before the history began, and the model's own output wasn't kept
        first = state
        entry.update(kind="edit", summary="The model's original output was not kept, so this is the transcript "
                                          "as it was when the history began")
    else:
        first = {"title": state["title"], "speaker_names": {}, "lines": lines}
        entry["summary"] = f"{len(lines)} line{'' if len(lines) == 1 else 's'} from " \
                           f"{job.get('model_title') or job.get('model')}"
    versions = [entry]
    _write(store, jid, versions, entry, first)
    if first != state:
        _add(store, jid, versions, first, state, "edit", time=_changed_at(store, jid),
             summary=f"Changes made before the history began: {describe(first, state)}")
    return versions


def ensure(store, jid, cfg=None):
    """The job's versions, oldest first, or None while it runs or has no lines. The first call for a
    finished job starts its history, and a transcript changed outside the app (by hand, or with an
    older Sedjem) is saved as a new version before anything else happens."""
    with store.lock:
        job = store.get(jid)
        if job is None or job["status"] in ACTIVE:
            return None
        state = live(store, jid, job)
        if state is None:
            return None
        folder = _folder(store, jid)
        index = read_json(folder / "index.json") if (folder / "index.json").exists() else None
        versions = index.get("versions") if isinstance(index, dict) else None
        if not versions:
            if folder.exists():  # unreadable, or left half written: keep it aside and start again
                folder.rename(folder.with_name(f"history-unreadable-{datetime.now():%Y%m%d-%H%M%S}"))
            return _start(store, jid, job, state, cfg)
        latest = _snapshot(store, jid, versions[-1]["n"])
        if state != latest:
            _add(store, jid, versions, latest, state, "edit", time=_changed_at(store, jid),
                 summary="Changes made outside the history" + (f": {describe(latest, state)}" if latest else ""))
        return versions


def current(store, jid, cfg=None):
    """The number of the current version, starting the history if needed; None if there is none.
    Never raises, so neither a finishing job nor the job page fails because of the history (it is
    started again on the next change)."""
    try:
        versions = ensure(store, jid, cfg)
    except Exception:  # see above
        return None
    return versions[-1]["n"] if versions else None


def _apply(store, jid, job, change, versions=None):
    """Write a change to transcript.json and job.json, where the rest of the app reads the transcript."""
    if "lines" in change:
        first = _original(store, jid, versions)
        data = store.transcript(jid) or {"model": job["model"]}
        data.update(lines=change["lines"], edited=first is None or change["lines"] != first["lines"])
        store.save_transcript(jid, data)
    fields = {k: change[k] for k in ("title", "speaker_names") if k in change}
    if fields:
        store.update(jid, **fields)


def save(store, jid, change, kind, message="", summary=None, cfg=None, merge=None, **extra):
    """Apply a change to the job (any of "title", "speaker_names", "lines") and keep the result as a new
    version. Returns the version's entry, or None if nothing changed. A quick change (kind "rename" or
    "merge"; merge describes a merge as in quick_summary) made soon after another one, both without a
    message, is folded into it. A job without a history (still running) just gets the change."""
    message = clean_message(message)
    with store.lock:
        job = store.get(jid)
        versions = ensure(store, jid, cfg)
        if versions is None:
            _apply(store, jid, job, change)
            return None
        last = versions[-1]
        old = _snapshot(store, jid, last["n"])
        new = {**old, **change}
        if new == old:
            return None
        _apply(store, jid, job, {k: v for k, v in new.items() if v != old.get(k)}, versions)
        merges = [merge] if merge else []
        if kind in QUICK and not message:
            if last["kind"] in QUICK and not last["message"] and _age(last) < FOLD_SECONDS:
                parent, both = _snapshot(store, jid, last["parent"]), last.get("merges", []) + merges
                if parent is not None and new != parent:  # (back to how it was: a version of its own)
                    last.update(time=now(), kind="merge" if both else "rename", summary=quick_summary(parent, new, both))
                    if both:
                        last["merges"] = both
                    _write(store, jid, versions, last, new)
                    return last
            summary = quick_summary(old, new, merges)
        if merges:
            extra["merges"] = merges
        return _add(store, jid, versions, old, new, kind, message, summary, **extra)


def preview(store, jid, change, cfg=None):
    """What saving a change would do, without saving it (to review edits first): the summary it would
    get and the differences (see compare); None while the job has no history."""
    with store.lock:
        versions = ensure(store, jid, cfg)
        if versions is None:
            return None
        old = _snapshot(store, jid, versions[-1]["n"])
    new = {**old, **change}
    return {"version": versions[-1]["n"], "summary": describe(old, new), "changes": compare(old, new)}


def _find(store, jid, n, cfg):
    versions = ensure(store, jid, cfg) or []
    entry = next((v for v in versions if v["n"] == n), None)
    return versions, entry, (_snapshot(store, jid, n) if entry else None)


def restore(store, jid, n, message="", cfg=None):
    """Make version n the current one again, as a new version (nothing is removed). Returns the new
    entry, or None if the transcript is already the same; raises KeyError if there is no version n."""
    with store.lock:
        versions, entry, state = _find(store, jid, n, cfg)
        if state is None:
            raise KeyError(n)
        latest = _snapshot(store, jid, versions[-1]["n"])
        return save(store, jid, state, "restore", message, f"Restored version {n} ({describe(latest, state)})",
                    cfg, restored_from=n)


def set_message(store, jid, n, message, cfg=None):
    """Add, change or clear the message of version n; returns the versions. KeyError if there is none."""
    with store.lock:
        versions, entry, _ = _find(store, jid, n, cfg)
        if entry is None:
            raise KeyError(n)
        entry["message"] = clean_message(message)
        _write(store, jid, versions, entry, None)
        return versions


def version(store, jid, n, cfg=None, against=None):
    """Version n: its entry, title, speaker names and lines, and what changed from version against (by
    default the version before it; "changes" is None for the first). None if either version is missing."""
    with store.lock:
        versions, entry, state = _find(store, jid, n, cfg)
        against = entry.get("parent") if entry and against is None else against
        before = _snapshot(store, jid, against) if any(v["n"] == against for v in versions) else None
        if state is None or (against is not None and before is None):
            return None
    return {"version": entry, **state, "against": against, "changes": compare(before, state) if before else None}


def at(store, jid, n, cfg=None):
    """(job, lines, edited) as they were at version n, for the exports, edited saying whether its lines
    differ from the model's; None if there is no version n."""
    with store.lock:
        versions, _, state = _find(store, jid, n, cfg)
        job = store.get(jid)
        first = _original(store, jid, versions)
    if state is None or job is None:
        return None
    return ({**job, "title": state["title"], "speaker_names": state["speaker_names"]}, state["lines"],
            first is None or state["lines"] != first["lines"])
