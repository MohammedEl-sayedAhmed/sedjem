"""Google Drive links: a file shared with anyone who has the link, downloaded with yt-dlp and transcribed like a file
on the computer.

inspect() reads a link to a file: its name and length. download() fetches it for a job: the file itself as Drive
keeps it (yt-dlp's "source" format, never a copy Drive made to play a video), as source.<ext>. The job then goes on
as an uploaded file does (app/jobs.py): its sound is converted, and a video gets the video.mp4 that plays beside the
transcript (app/video.py). Drive links have no streaming and no captions. Before a large file, Drive answers with a
page saying it can't scan the file for viruses; yt-dlp's GoogleDrive extractor submits that page's form and
downloads the file.

A link to a Drive folder is recognised and refused (FOLDERS_LATER), before yt-dlp sees it: yt-dlp's folder extractor
fails on Drive as it is now (yt-dlp issue 10484), so folders wait until it works again.

yt-dlp is used as for YouTube links (app/youtube.py's _ydl and _extract), with lists of Drive's own: the links it
takes (HOSTS), its GoogleDrive extractor and no other, so its generic one never runs (EXTRACTORS), and the hosts a
file may come from (DOWNLOAD_HOSTS and DOWNLOAD_DOMAINS), checked on the address yt-dlp read the link from and again
on the answer to the download itself, after any redirect. Nothing signs in to Google, so a file that isn't shared
with anyone who has the link is refused, and so is one that was removed (PRIVATE).
"""
import re
import shutil
import urllib.parse
from pathlib import Path

from . import youtube
from .engines import Cancelled

HOSTS = {"drive.google.com", "docs.google.com"}  # the links it takes
EXTRACTORS = ("googledrive",)  # yt-dlp's only: its generic one, and its folder one, never run
EXTRACTOR_KEYS = {"GoogleDrive"}
DOWNLOAD_HOSTS = {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}  # where a file comes from,
DOWNLOAD_DOMAINS = (".googleusercontent.com",)  # or where Drive sends its download on to
DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{28,100}")  # a file's or a folder's id (yt-dlp's take 28 or more)
NOT_DRIVE = "paste the link of a Google Drive file"
FOLDERS_LATER = "Google Drive folders can't be read yet. Add the files one by one, each with its own link."
PRIVATE = "This file isn't shared with anyone who has the link. Sedjem can't sign in to Google Drive yet."
NO_DOWNLOAD = "Google Drive doesn't offer this file for download; its owner may have turned downloading off"
NOT_MEDIA = "this file isn't audio or video"
ELSEWHERE = "Google Drive sent the download to another site ({}), so nothing of it was kept"
NO_ROOM = "not enough free disk space for this file"
OTHER_FILES = {  # the extensions of files that are clearly not audio or video: documents, images, archives, programs
    "pdf", "doc", "docx", "odt", "rtf", "txt", "md", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp", "key", "pages",
    "numbers", "epub", "mobi", "html", "htm", "xml", "json", "srt", "vtt", "ass", "jpg", "jpeg", "png", "gif", "bmp",
    "webp", "heic", "svg", "tif", "tiff", "psd", "ai", "eps", "zip", "rar", "7z", "gz", "tar", "exe", "msi", "dmg",
    "apk", "iso", "py", "js", "ipynb"}
MARGIN = 1e9                # bytes kept free on the disk, beyond the file and the sound made from it
SOUND_SHARE = 0.2           # the sound made from a file takes up to this share of its size again
CHECK_EVERY = 32 * 1024 ** 2  # without a size from Drive, the free space is checked each time this much has come


class DriveError(Exception):
    pass


# ---- links -----------------------------------------------------------------------------------------

def file_url(fid):
    return f"https://drive.google.com/file/d/{fid}/view"


def _account(parts):
    """The parts of a path after an account's u/<number>, as in drive/u/0/folders/<id>."""
    return parts[2:] if parts[:1] == ["u"] and len(parts) > 1 and parts[1].isdigit() else parts


def parse(url):
    """(kind, id) of a Google Drive link: "file" (…/file/d/<id>/view, …/uc?id=<id>, …/open?id=<id>) or "folder"
    (…/drive/folders/<id>, also with u/0/ or mobile/ before folders). Raises DriveError for anything else: another
    site, a host that only looks like Drive's, a user name, password or odd port in the address, a Google Docs file,
    Drive's own pages."""
    url = str(url or "").strip()
    url = youtube.with_scheme(url)
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port
    except ValueError:
        raise DriveError(NOT_DRIVE)
    host = (p.hostname or "").lower()
    if p.scheme.lower() not in ("http", "https") or host not in HOSTS or "@" in p.netloc or port not in (None, 80, 443):
        raise DriveError(NOT_DRIVE)
    parts = [x for x in p.path.split("/") if x]
    kind, fid = None, ""
    if parts[:1] == ["drive"]:
        rest = _account(parts[1:])
        rest = rest[1:] if rest[:1] == ["mobile"] else rest
        if len(rest) == 2 and rest[0] == "folders":
            kind, fid = "folder", rest[1]
    elif parts[:1] == ["file"]:
        rest = _account(parts[1:])
        if len(rest) in (2, 3) and rest[0] == "d":  # …/d/<id>, then view, edit or preview
            kind, fid = "file", rest[1]
    elif parts in (["open"], ["uc"]):  # (Drive's older open?id= links can be folders too; they are read as files)
        kind, fid = "file", (urllib.parse.parse_qs(p.query).get("id") or [""])[0]
    elif parts[:1] in (["document"], ["spreadsheets"], ["presentation"], ["forms"]):
        raise DriveError("this is a Google Docs file, not audio or video")
    if not kind or not DRIVE_ID.fullmatch(fid):
        raise DriveError(NOT_DRIVE)
    return kind, fid


def check_url(url):
    """A Drive file's link rebuilt from its id, …/file/d/<id>/view, so nothing else of it reaches yt-dlp. A folder's
    link is refused with FOLDERS_LATER."""
    kind, fid = parse(url)
    if kind == "folder":
        raise DriveError(FOLDERS_LATER)
    return file_url(fid)


def download_allowed(url):
    """Whether a download address yt-dlp gave back is on one of Drive's own hosts."""
    return youtube.on_hosts(url, DOWNLOAD_HOSTS) or youtube.on_hosts(url, DOWNLOAD_DOMAINS)


def extension(name):
    """The extension of a file's name in lower case, without the dot ("" when it has none). It has a letter: in
    "Week 1.2" or "Meeting 2026.11.19" the end is part of the name, not an extension."""
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", str(name or ""))
    return m.group(1).lower() if m and re.search(r"[A-Za-z]", m.group(1)) else ""


def clearly_not_media(name, info):
    """Whether a file is clearly not audio or video: its name ends in the extension of another kind of file (a
    document, an image, an archive), and Drive gives no sign that it plays, neither a length nor a copy made to play
    it. Anything else is taken, and a file that turns out not to be audio or video fails when its sound is read."""
    plays = isinstance(info.get("duration"), (int, float)) and info["duration"] > 0 \
        or any(f.get("format_id") != "source" for f in info.get("formats") or [])
    return extension(name) in OTHER_FILES and not plays


def clean_error(e):
    """yt-dlp's message in words (app/youtube.py's plain_error). Drive answers a file it won't show to someone who
    isn't signed in as it answers one that doesn't exist: either is PRIVATE."""
    msg = youtube.plain_error(e)
    if re.search(r"HTTP Error 40[0134]\b|accounts\.google\.com", msg):
        return PRIVATE
    return msg


SITE = youtube.Site(check_url, EXTRACTOR_KEYS, HOSTS, DriveError, clean_error,
                    "the link did not lead to a Google Drive file")


# ---- yt-dlp ----------------------------------------------------------------------------------------

def _ydl(cfg, **extra):
    """yt-dlp as YouTube links use it (app/youtube.py's _ydl), with Drive's file extractor and no other."""
    return youtube._ydl(cfg, allowed_extractors=[re.escape(x) for x in EXTRACTORS], **extra)


def _extract(ydl, url):
    return youtube._extract(ydl, url, SITE)


# ---- reading a link ---------------------------------------------------------------------------------

def inspect(cfg, url):
    """A link's file, in the shape of a YouTube video's (app/youtube.py's inspect), for the same form: {"site"
    ("drive"), "kind" ("file"), "id", "title" (its name without the extension, as an uploaded file's), "name", "url",
    "count", "duration", "entries" (the file, as the one entry)}."""
    check_url(url)  # a folder's link is refused before anything is asked of Drive
    with _ydl(cfg) as ydl:
        info = _extract(ydl, url)
    fid = str(info.get("id") or "")
    if "entries" in info or not DRIVE_ID.fullmatch(fid) or file_url(fid) != check_url(url):
        raise DriveError(SITE.elsewhere)
    name = str(info.get("title") or fid)
    if clearly_not_media(name, info):
        raise DriveError(NOT_MEDIA)
    if not pick_source(info.get("formats")):
        raise DriveError(NO_DOWNLOAD)
    duration = info.get("duration") if isinstance(info.get("duration"), (int, float)) else None
    title = (name[:-len(extension(name)) - 1].strip() if extension(name) else name) or name
    entry = {"id": fid, "name": name, "title": title, "index": 1,
             "duration": duration, "url": file_url(fid), "available": True, "live": False, "captions": None, "use": None}
    return {"site": "drive", "kind": "file", "id": fid, "title": entry["title"], "name": name, "url": entry["url"],
            "count": 1, "duration": duration, "entries": [entry]}


# ---- downloading -----------------------------------------------------------------------------------

def pick_source(formats):
    """The file itself (yt-dlp's "source" format), when Drive offers it for download from one of its own hosts;
    else None. The copies Drive makes to play a video in its page are never taken."""
    return next((f for f in formats or [] if f.get("format_id") == "source" and download_allowed(f.get("url"))
                 and str(f.get("protocol") or "https") in ("https", "http")), None)


def free_space(folder):
    return shutil.disk_usage(folder).free


def _room(cfg, folder, total, left):
    """DriveError when a file of `total` bytes is over the upload limit, or the disk hasn't space for the `left` bytes
    still to come, the sound made from them and MARGIN, as for an upload (app/server.py)."""
    if total > cfg.max_upload:
        raise DriveError(f"the file is larger than the {cfg.max_upload // 1024 ** 3} GB limit")
    if free_space(folder) < left * (1 + SOUND_SHARE) + MARGIN:
        raise DriveError(NO_ROOM)


def _only_from_drive(ydl):
    """Make the download refuse an answer that doesn't come from Drive's hosts. yt-dlp's downloader opens the
    address through ydl.urlopen, which follows redirects; the answer's address is the one after them, and it is
    checked before anything of its body is read. (The other site has had the request by then: only what it sends
    back is refused.)"""
    urlopen = ydl.urlopen

    def checked(request, *args, **kwargs):
        answer = urlopen(request, *args, **kwargs)
        if not download_allowed(answer.url):
            answer.close()
            raise DriveError(ELSEWHERE.format(urllib.parse.urlsplit(answer.url).hostname or "an unknown site"))
        return answer
    ydl.urlopen = checked


def download(cfg, job, folder, on_progress, cancelled):
    """Download a job's file into its folder as source.<ext>, with the extension of its name on Drive (bin without
    one). Returns the field the job records: "source_name", the file's name. on_progress(done, total) counts bytes;
    cancelled() is checked while it runs.

    It stops, and deletes what came, as soon as the file is too large. When Drive gives the file's size as the
    download starts: at once, if the file is over the upload limit (cfg.max_upload) or would leave less than MARGIN
    free beside the sound made from it. When Drive gives no size: once more than the upload limit has come, or when
    the free space, checked each CHECK_EVERY bytes, is less than MARGIN beside the sound made from what came. It
    also stops, keeping nothing, when the download's answer comes from another site than Drive's (_only_from_drive)."""
    folder = Path(folder)
    fid = str(job.get("drive_id") or "")
    if not DRIVE_ID.fullmatch(fid):
        raise DriveError("this transcription has no Google Drive file")
    with _ydl(cfg) as ydl:
        info = _extract(ydl, job.get("source_url"))
        if "entries" in info or str(info.get("id") or "") != fid:
            raise DriveError("the link led to another file than the one added")
        source = pick_source(info.get("formats"))
        if not source:
            raise DriveError(NO_DOWNLOAD)
        name = str(info.get("title") or job.get("source_name") or fid)
        path = folder / f"source.{extension(name) or 'bin'}"
        state = {"sized": False, "next": 0}

        def hook(d):
            if cancelled():
                raise Cancelled()
            if d.get("status") != "downloading":
                return
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or None
            if total and not state["sized"]:  # Drive says how large it is: checked once, as the download starts
                state["sized"] = True
                _room(cfg, folder, total, total - done)
            elif not total:  # it doesn't say: the limit, and the free space as the file comes
                if done > cfg.max_upload:
                    _room(cfg, folder, done, 0)
                if done >= state["next"]:
                    state["next"] = done + CHECK_EVERY
                    if free_space(folder) < done * SOUND_SHARE + MARGIN:
                        raise DriveError(NO_ROOM)
            on_progress(done, total)

        ydl.add_progress_hook(hook)
        _only_from_drive(ydl)
        try:
            ydl.dl(str(path), {**source, "id": fid, "title": name})
        except Cancelled:
            raise
        except DriveError:  # too large, or from elsewhere: what came so far isn't kept
            for part in folder.glob(f"{path.name}.*"):
                part.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001 — the network, Drive refusing, the disk
            raise DriveError(f"the download failed: {clean_error(e)}")
        if cancelled():
            raise Cancelled()
        if not path.exists():
            raise DriveError("the download failed: Google Drive sent nothing")
    return {"source_name": name}
