"""Recordings on this computer as a course: the recordings in a folder, or several files picked together. Each is
read in place where it is, as a single file's path is (app/server.py create_job), so nothing is copied.

list_folder() reads a folder by its real path (every symbolic link followed), and only its direct entries:
subfolders aren't read. Its recordings are the files with a recording's extension (app/assistants.py
RECORDING_TYPES), listed in natural name order, so "Lecture 2" comes before "Lecture 10" (app/drive.py natural_key).
Subfolders, other files and hidden files (whose names start with a dot, such as the ._ files macOS leaves on other
disks) are skipped and counted. A symbolic link counts as what it leads to: a link to a folder is a subfolder, and a
link to a file is that file only when the file is itself directly in the folder (then it is listed once). A link to
a file anywhere else is skipped and counted, as an assistant's path that leads out of an allowed folder is refused
(app/assistants.py recording_path), and a link that leads to nothing counts as another file. Only links are
followed one by one: the folder's own entries are already in its real path, so a folder of many files is read
quickly. The first MAX_FILES recordings are listed, as a playlist's videos are.

list_files() does the same for paths picked together (the desktop app's file picker): each by its real path, a
folder among them counted and skipped, and the same for a file that isn't a recording. list_names() does it for the
names of files dropped or picked together in a browser, which the page then uploads, so the page lists them by the
same extensions and in the same order without a copy of either.

A course's id is "folder-" or "files-" and 20 random hexadecimal digits. A folder's course keeps the folder's real
path in job.json, so adding the same folder again joins its course (folder_course); the id doesn't come from the
path, so it tells nothing about the path to someone who sees only the id. Files picked together make a new course
each time, numbered 1, 2, 3 in the order of their names. A folder's course is named after the folder; files picked
together get the name the user gives, or "Files added on <day>" (files_course). Assistants see a course's title and
each lecture's title, never a path (app/mcp.py).
"""
import os
import re
import secrets
from datetime import datetime

from . import youtube
from .assistants import RECORDING_TYPES
from .drive import natural_key

MAX_FILES = youtube.MAX_ENTRIES  # a folder's recordings are listed up to this many, as a playlist's videos
MAX_PATHS = 5000                 # paths picked together in one request
MAX_PATH = 4096                  # characters in a path
MAX_TITLE = 200                  # characters in a course's name
COURSE_ID = re.compile(r"(folder|files)-[0-9a-f]{20}")
FILES_ID = re.compile(r"files-[0-9a-f]{20}")
EXAMPLE = os.path.join(os.path.expanduser("~"), "Lectures")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class LocalError(Exception):
    pass


def new_id(kind):
    return f"{kind}-{secrets.token_hex(10)}"


def is_recording(name):
    """Whether a file's name is a recording's: one of the extensions the app takes, and not a hidden file."""
    name = os.path.basename(str(name))
    return not name.startswith(".") and os.path.splitext(name)[1].lower() in RECORDING_TYPES


def full_path(path, what="folder"):
    """A typed path, with ~ for the home folder, once it is absolute; LocalError saying what to give else."""
    if not isinstance(path, str) or not path.strip() or "\0" in path or len(path) > MAX_PATH:
        raise LocalError(f"give the {what}'s full path, such as {EXAMPLE}")
    path = os.path.expanduser(path.strip())
    if not os.path.isabs(path):
        raise LocalError(f"give the {what}'s full path, such as {EXAMPLE}, not {path}")
    return path


def _key(path):
    return os.path.normcase(path)


def _ordered(found):
    """[(name, real path)] in natural name order, each real path once."""
    out, seen = [], set()
    for name, real in sorted(found, key=lambda f: (natural_key(f[0]), f[0], f[1])):
        if _key(real) not in seen:
            seen.add(_key(real))
            out.append((name, real))
    return out


def list_folder(path):
    """A folder's recordings: {"path" (its real path), "name", "files" ([(name, real path)] in natural name order),
    "folders" (how many subfolders), "others" (other files, and links that lead to nothing), "outside" (links to files
    outside the folder)}. LocalError when it isn't there, is a file, or can't be read."""
    given = full_path(path)
    real = os.path.realpath(given)
    if not os.path.exists(real):
        raise LocalError(f"there is no folder at {given}")
    if not os.path.isdir(real):
        raise LocalError(f"{given} is a file, not a folder")
    found, folders, others, outside = [], 0, 0, 0
    try:
        with os.scandir(real) as entries:
            for entry in entries:
                kind = _entry(entry, real)
                if kind == "folder":
                    folders += 1
                elif kind == "outside":
                    outside += 1
                elif kind:
                    found.append(kind)
                else:
                    others += 1
    except PermissionError:
        raise LocalError(f"Sedjem isn't allowed to read the folder {given}")
    except OSError as e:
        raise LocalError(f"the folder {given} couldn't be read: {e.strerror or e}")
    return {"path": real, "name": os.path.basename(real) or real, "files": _ordered(found), "folders": folders,
            "others": others, "outside": outside}


def _entry(entry, real):
    """What a folder's entry is: "folder" (a subfolder, or a link to a folder), "outside" (a link to a file outside the
    folder), (name, real path) for a recording, or None for anything else (another file, a hidden file, a link that
    leads to nothing, an entry gone since the folder was read). real: the folder's real path, so only a link needs
    os.path.realpath."""
    try:
        if entry.is_dir():  # (a link to a folder too)
            return "folder"
        if not entry.is_symlink():
            return (entry.name, entry.path) if entry.is_file() and is_recording(entry.name) else None
        target = os.path.realpath(entry.path)
        if not os.path.exists(target):  # a link that leads to nothing
            return None
        if _key(os.path.dirname(target)) != _key(real):  # a link that leads out of the folder
            return "outside"
        return (os.path.basename(target), target) if os.path.isfile(target) and is_recording(target) else None
    except OSError:  # gone since the folder was read, or not readable
        return None


def list_files(paths):
    """Paths picked together: {"files" ([(name, real path)] of the recordings in natural name order), "folders"
    (how many of the paths are folders), "others" (other files)}. LocalError when one isn't there."""
    if not isinstance(paths, list) or not paths:
        raise LocalError("give the files' full paths")
    if len(paths) > MAX_PATHS:
        raise LocalError(f"pick at most {MAX_PATHS} files at once")
    found, folders, others = [], 0, 0
    for path in paths:
        given = full_path(path, "file")
        real = os.path.realpath(given)
        if not os.path.exists(real):
            raise LocalError(f"there is no file at {given}")
        if os.path.isdir(real):
            folders += 1
        elif os.path.isfile(real) and is_recording(real):
            found.append((os.path.basename(real), real))
        else:
            others += 1
    return {"files": _ordered(found), "folders": folders, "others": others}


def list_names(names, folders=0):
    """The names of files dropped or picked together in a browser, as list_files gives paths: {"files" ([(name, its
    place in names as text)] of the recordings in natural name order), "folders" (the folders dropped with them, which
    the page counts), "others"}."""
    if not isinstance(names, list) or not names \
            or not all(isinstance(n, str) and 0 < len(n) <= MAX_PATH for n in names):
        raise LocalError("give the files' names")
    if len(names) > MAX_PATHS:
        raise LocalError(f"pick at most {MAX_PATHS} files at once")
    if not isinstance(folders, int) or isinstance(folders, bool) or folders < 0:
        raise LocalError("folders must be a number")
    found = [(os.path.basename(n), str(i)) for i, n in enumerate(names) if is_recording(n)]
    return {"files": _ordered(found), "folders": folders, "others": len(names) - len(found)}


def skipped(found):
    """What a listing skipped, as the page and the API give it: {"folders", "files", and "links" for a folder}."""
    out = {"folders": found["folders"], "files": found["others"]}
    if "outside" in found:
        out["links"] = found["outside"]
    return out


def words(skips, folder=True):
    """What a listing skipped, in words: "2 subfolders, 3 other files and 1 link to a file outside the folder", or
    "", as app/drive.py skipped_words says a Drive folder's. folder=False: files picked together, whose folders
    aren't subfolders."""
    n, m, k = (skips.get(x) or 0 for x in ("folders", "files", "links"))
    parts = [f"{n} {'sub' if folder else ''}folder{'' if n == 1 else 's'}" if n else "",
             f"{m} other file{'' if m == 1 else 's'}" if m else "",
             f"{k} link{'' if k == 1 else 's'} to {'a file' if k == 1 else 'files'} outside the folder" if k else ""]
    parts = [p for p in parts if p]
    return ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else "".join(parts)


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _entries(files, sizes=True):
    """The first MAX_FILES of [(name, id)], numbered: each {"id", "name", "title" (the name without its extension, as
    a single file's title), "index", "size" (bytes, when the id is a path)}."""
    return [{"id": fid, "name": name, "title": os.path.splitext(name)[0] or name, "index": i,
             "size": _size(fid) if sizes else None}
            for i, (name, fid) in enumerate(files[:MAX_FILES], 1)]


def inspect_folder(path):
    """A folder's recordings, in the shape of a Drive folder's (app/drive.py inspect_folder), for the same list:
    {"kind" ("folder"), "title" (the folder's name), "path" (its real path), "count", "entries" (each with "id", its
    real path, "name", "title" (the name without its extension) and "index", in natural name order), "skipped"
    ({"folders", "files", "links"}), "cut" (more than MAX_FILES were found)}. LocalError when it has none."""
    found = list_folder(path)
    skips = skipped(found)
    if not found["files"]:
        said = words(skips)
        raise LocalError(f"this folder has no recordings ({said} skipped)" if said else "this folder is empty")
    entries = _entries(found["files"])
    return {"kind": "folder", "title": found["name"], "path": found["path"], "count": len(entries),
            "entries": entries, "skipped": skips, "cut": len(found["files"]) > MAX_FILES}


def _picked(found, sizes=True):
    """Files picked together, in the same shape as a folder's with "kind" "files", and as "title" the name their
    course gets unless the user gives another (course_title). LocalError when none is a recording."""
    skips = skipped(found)
    if not found["files"]:
        raise LocalError(f"none of these files is a recording ({words(skips, folder=False)} skipped)")
    entries = _entries(found["files"], sizes)
    return {"kind": "files", "title": course_title(""), "count": len(entries), "entries": entries, "skipped": skips,
            "cut": len(found["files"]) > MAX_FILES}


def inspect_files(paths):
    """Paths picked together (list_files), each entry's id its real path."""
    return _picked(list_files(paths))


def inspect_names(names, folders=0):
    """Names of files from a browser (list_names), each entry's id its place in names."""
    return {**_picked(list_names(names, folders), sizes=False), "upload": True}


def inspect(path=None, paths=None, names=None, folders=0):
    """What POST /api/local/inspect answers: a folder's recordings, a file on its own ({"kind": "file"}, which the
    caller fills in), paths picked together, or names of files from a browser."""
    if names is not None:
        return inspect_names(names, folders)
    if paths is not None:
        return inspect_files(paths)
    given = full_path(path, "file or folder")
    real = os.path.realpath(given)
    if not os.path.exists(real):
        raise LocalError(f"there is no file or folder at {given}")
    if os.path.isfile(real):
        return {"kind": "file", "path": given}
    return inspect_folder(given)


# ---- courses ---------------------------------------------------------------------------------------

def day(when=None):
    when = when or datetime.now()
    return f"{when.day} {MONTHS[when.month - 1]} {when.year}"


def course_title(text):
    """A course's name as the user typed it, on one line; "Files added on <day>" when there is none."""
    return " ".join(str(text or "").split())[:MAX_TITLE] or f"Files added on {day()}"


def folder_course(store, found):
    """The course of a folder's recordings (inspect_folder): the course already made from the same folder, by its
    real path, or a new one. {"id", "title", "count", "local": "folder", "path"}."""
    cid = None
    for job in store.list():
        c = job.get("course") if isinstance(job.get("course"), dict) else {}
        if c.get("local") == "folder" and COURSE_ID.fullmatch(str(c.get("id") or "")) \
                and isinstance(c.get("path"), str) and _key(c["path"]) == _key(found["path"]):
            cid = c["id"]
            break
    return {"id": cid or new_id("folder"), "title": found["title"], "count": found["count"], "local": "folder",
            "path": found["path"]}


def files_course(title, count, cid=None):
    """The course of files picked together: {"id", "title", "count", "local": "files"}, with a new id unless cid,
    one already made (FILES_ID), is given."""
    if cid is not None and not FILES_ID.fullmatch(str(cid)):
        raise LocalError("that isn't the id of files added together")
    return {"id": cid or new_id("files"), "title": course_title(title), "count": count, "local": "files"}


def upload_course(p):
    """The course of an uploaded file that is one of several added together, from POST /api/jobs's parameters
    course_title, course_index (its number), course_count and course (the id the first one's answer gave; none for
    the first): the course with its "index", or None for a file on its own."""
    if not any(k in p for k in ("course", "course_title", "course_index", "course_count")):
        return None
    try:
        index, count = int(p.get("course_index")), int(p.get("course_count"))
    except (TypeError, ValueError):
        raise LocalError("course_index and course_count must be numbers")
    if not 1 <= index <= count <= MAX_FILES:
        raise LocalError(f"course_index must be from 1 to course_count, which is at most {MAX_FILES}")
    return {**files_course(p.get("course_title"), count, p.get("course") or None), "index": index}


def is_local(course):
    """Whether a job's course is one of these: a folder's, or files added together."""
    return isinstance(course, dict) and course.get("local") in ("folder", "files") \
        and bool(COURSE_ID.fullmatch(str(course.get("id") or "")))
