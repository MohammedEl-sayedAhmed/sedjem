"""Google Drive links: a file shared with anyone who has the link, downloaded with yt-dlp and transcribed like a file
on the computer, or a shared folder, whose audio and video files become a course.

inspect() reads a link to a file: its name and length. download() fetches it for a job: the file itself as Drive
keeps it (yt-dlp's "source" format, never a copy Drive made to play a video), as source.<ext>. The job then goes on
as an uploaded file does (app/jobs.py): its sound is converted, and a video gets the video.mp4 that plays beside the
transcript (app/video.py). Drive links have no streaming and no captions. Before a large file, Drive answers with a
page saying it can't scan the file for viruses; yt-dlp's GoogleDrive extractor submits that page's form and
downloads the file.

inspect() also reads a link to a folder, without yt-dlp: its folder extractor fails on Drive as it is now (yt-dlp
issue 10484). Sedjem asks Drive's embedded view of the folder (LISTING), a plain HTML page that Drive serves for a
folder shared with anyone who has the link, and reads it with html.parser: the folder's name, and each entry's link,
type and name. Its audio and video files are listed in natural name order ("Lecture 2" before "Lecture 10"), and its
subfolders and other files are counted and skipped. The files picked become one course, each a lecture that is then
downloaded as a Drive file on its own. The view has no sizes or lengths, and Google can change it without notice.

yt-dlp is used as for YouTube links (app/youtube.py's _ydl and _extract), with lists of Drive's own: the links it
takes (HOSTS), its GoogleDrive extractor and no other, so its generic one never runs (EXTRACTORS), and the hosts a
file may come from (DOWNLOAD_HOSTS and DOWNLOAD_DOMAINS), checked on the address yt-dlp read the link from and again
on the answer to the download itself, after any redirect. A folder's page is asked of drive.google.com alone, and a
redirect is never followed. Nothing signs in to Google, so a file or folder that isn't shared with anyone who has the
link is refused, and so is one that was removed (PRIVATE, PRIVATE_FOLDER).
"""
import http.client
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from . import youtube
from .assistants import RECORDING_TYPES
from .engines import Cancelled

HOSTS = {"drive.google.com", "docs.google.com"}  # the links it takes
EXTRACTORS = ("googledrive",)  # yt-dlp's only: its generic one, and its folder one, never run
EXTRACTOR_KEYS = {"GoogleDrive"}
DOWNLOAD_HOSTS = {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}  # where a file comes from,
DOWNLOAD_DOMAINS = (".googleusercontent.com",)  # or where Drive sends its download on to
DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{28,100}")  # a file's or a folder's id (yt-dlp's take 28 or more)
NOT_DRIVE = "paste the link of a Google Drive file or folder"
NOT_A_FILE = "this is the link of a Google Drive folder, not of a file"
PRIVATE = "This file isn't shared with anyone who has the link. Sedjem can't sign in to Google Drive yet."
PRIVATE_FOLDER = "This folder isn't shared with anyone who has the link. Sedjem can't sign in to Google Drive yet."
BUSY = "Google Drive is limiting this file for now, as it does when many people open it. Try again later."
BUSY_FOLDER = "Google Drive is limiting this folder for now, as it does when many people open it. Try again later."
NOT_LISTED = "Google Drive answered with a page that isn't the folder's list of files, so the folder couldn't be read"
TOO_LONG = "this folder's list of files is too long to read; add its files, or its subfolders, on their own"
NO_DOWNLOAD = "Google Drive doesn't offer this file for download; its owner may have turned downloading off"
NOT_MEDIA = "this file isn't audio or video"
ELSEWHERE = "Google Drive sent the download to another site ({}), so nothing of it was kept"
NO_ROOM = "not enough free disk space for this file"
OTHER_FILES = {  # the extensions of files that are clearly not audio or video: documents, images, archives, programs
    "pdf", "doc", "docx", "odt", "rtf", "txt", "md", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp", "key", "pages",
    "numbers", "epub", "mobi", "html", "htm", "xml", "json", "srt", "vtt", "ass", "jpg", "jpeg", "png", "gif", "bmp",
    "webp", "heic", "svg", "tif", "tiff", "psd", "ai", "eps", "zip", "rar", "7z", "gz", "tar", "exe", "msi", "dmg",
    "apk", "iso", "py", "js", "ipynb"}
LISTING = "https://drive.google.com/embeddedfolderview"  # Drive's embedded view of a folder: plain HTML, no sign-in
LISTING_HOSTS = {"drive.google.com"}  # where a folder's page may come from (no redirect is followed)
ICON_HOST = "drive-thirdparty.googleusercontent.com"  # a file's icon there names its type: /16/type/<MIME type>
MIME = re.compile(r"[a-z0-9][a-z0-9.+-]{0,126}/[a-z0-9][a-z0-9.+-]{0,126}")
RESOURCE_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")  # a folder link's resourcekey=, passed on to the listing
GENERIC = ("", "application/octet-stream")  # no type, or none Drive knew: the file's extension decides
FOLDER_TIMEOUT = 30         # seconds to wait for a folder's page
MAX_PAGE = 8 * 1024 ** 2    # a folder's page is read up to this many bytes (about 6,000 entries)
MAX_FILES = youtube.MAX_ENTRIES  # a folder's audio and video files are listed up to this many, as a playlist's videos
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
    link is refused: yt-dlp never reads folders (list_folder does)."""
    kind, fid = parse(url)
    if kind == "folder":
        raise DriveError(NOT_A_FILE)
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
    isn't signed in as it answers one that doesn't exist: either is PRIVATE. Too many requests (429) is Drive's limit
    on a file many people have opened lately: BUSY."""
    msg = youtube.plain_error(e)
    if re.search(r"HTTP Error 40[0134]\b|accounts\.google\.com", msg):
        return PRIVATE
    if re.search(r"HTTP Error 429\b|Too Many Requests", msg):
        return BUSY
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

def title_of(name):
    """A file's name without its extension, as an uploaded file's title is."""
    return (name[:-len(extension(name)) - 1].strip() if extension(name) else name) or name


def inspect(cfg, url):
    """A link's file, in the shape of a YouTube video's (app/youtube.py's inspect), for the same form: {"site"
    ("drive"), "kind" ("file"), "id", "title" (its name without the extension, as an uploaded file's), "name", "url",
    "count", "duration", "entries" (the file, as the one entry)}. A folder's link gives its files instead
    (inspect_folder), and yt-dlp isn't asked."""
    if parse(url)[0] == "folder":
        return inspect_folder(url)
    check_url(url)
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
    entry = {"id": fid, "name": name, "title": title_of(name), "index": 1,
             "duration": duration, "url": file_url(fid), "available": True, "live": False, "captions": None, "use": None}
    return {"site": "drive", "kind": "file", "id": fid, "title": entry["title"], "name": name, "url": entry["url"],
            "count": 1, "duration": duration, "entries": [entry]}


# ---- folders ---------------------------------------------------------------------------------------

def folder_url(fid):
    return f"https://drive.google.com/drive/folders/{fid}"


def folder_link(url):
    """(id, resource key or None) of a link to a Drive folder; DriveError for anything else. The resource key is the
    link's resourcekey=, which Drive puts in the links of some folders shared before 2021."""
    kind, fid = parse(url)
    if kind != "folder":
        raise DriveError(NOT_DRIVE)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(youtube.with_scheme(str(url).strip())).query)
    key = (query.get("resourcekey") or [None])[0]
    if key is not None and not RESOURCE_KEY.fullmatch(key):
        raise DriveError(NOT_DRIVE)
    return fid, key


def listing_url(fid, key=None):
    return LISTING + "?" + urllib.parse.urlencode({"id": fid, **({"resourcekey": key} if key else {})})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A folder's page comes from the address asked for or not at all: a redirect is an answer, never followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def _folder_page(fid, key=None):
    """The HTML of Drive's embedded view of a folder, from drive.google.com. Drive answers a folder it won't show to
    someone who isn't signed in as it answers one that doesn't exist (404), or sends them to sign in: either is
    PRIVATE_FOLDER. Too many requests (429) is BUSY_FOLDER."""
    request = urllib.request.Request(listing_url(fid, key), headers={"User-Agent": "Sedjem", "Accept": "text/html"})
    try:
        with OPENER.open(request, timeout=FOLDER_TIMEOUT) as answer:
            if answer.status != 200 or not youtube.on_hosts(answer.url, LISTING_HOSTS) \
                    or answer.headers.get_content_type() != "text/html":
                raise DriveError(NOT_LISTED)
            body = answer.read(MAX_PAGE + 1)
    except urllib.error.HTTPError as e:
        to = e.headers.get("Location") if e.headers else None
        e.close()
        if e.code in (400, 401, 403, 404) or 300 <= e.code < 400 and youtube.link_host(to) == "accounts.google.com":
            raise DriveError(PRIVATE_FOLDER)
        if e.code == 429:
            raise DriveError(BUSY_FOLDER)
        raise DriveError(f"Google Drive didn't list the folder: it answered {e.code}")
    except (OSError, ValueError, http.client.HTTPException) as e:  # no connection, a timeout, an answer cut short
        raise DriveError(f"Google Drive couldn't be reached: {getattr(e, 'reason', None) or e}")
    if len(body) > MAX_PAGE:
        raise DriveError(TOO_LONG)
    return body.decode("utf-8", errors="replace")


def icon_type(src):
    """The MIME type a file's icon names (…googleusercontent.com/16/type/video/mp4 is video/mp4), or ""."""
    try:
        p = urllib.parse.urlsplit(str(src or ""))
    except ValueError:
        return ""
    parts = p.path.split("/")
    if p.scheme != "https" or (p.hostname or "").lower() != ICON_HOST or len(parts) < 5 or parts[2] != "type":
        return ""
    mime = urllib.parse.unquote("/".join(parts[3:])).lower()
    return mime if MIME.fullmatch(mime) else ""


class FolderPage(HTMLParser):
    """Drive's embedded view of a folder, read tag by tag: the folder's name (the page's title), whether the page has
    the list of entries at all (listed), and its entries in the page's order, each {"href" (its link), "type" (the
    MIME type its icon names, "" without one), "folder" (its icon says Folder), "name"}. Each entry is a div of class
    flip-entry, with a link, an icon and a div of class flip-entry-title."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.listed, self.entries = "", False, []
        self._text = None  # what the text being read belongs to: "title", "name", or None
        self._entry = None

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        classes = a.get("class", "").split()
        if tag == "title" and not self.title and self._entry is None:
            self._text = "title"
        elif tag == "div" and "flip-entries" in classes:
            self.listed = True
        elif tag == "div" and "flip-entry" in classes:
            self._entry = {"href": "", "type": "", "folder": False, "name": ""}
            self.entries.append(self._entry)
        elif self._entry is None:
            return
        elif tag == "a" and not self._entry["href"]:
            self._entry["href"] = a.get("href", "")
        elif tag == "img":
            self._entry["type"] = icon_type(a.get("src")) or self._entry["type"]
        elif tag == "div" and "flip-entry-title" in classes:
            self._entry["name"], self._text = "", "name"
        elif a.get("aria-label") == "Folder":
            self._entry["folder"] = True

    def handle_endtag(self, tag):
        if tag in ("title", "div"):
            self._text = None

    def handle_data(self, data):
        if self._text == "title":
            self.title += data
        elif self._text == "name":
            self._entry["name"] += data


def is_media(name, mime):
    """Whether a file in a folder is audio or video: by the MIME type Drive gives it, or, when Drive gives none or
    only a generic one, by its name's extension, one of the recordings the app takes (app/assistants.py)."""
    if mime.startswith(("audio/", "video/")):
        return True
    return mime in GENERIC and f".{extension(name)}" in RECORDING_TYPES


def natural_key(name):
    """A name's place in natural order: its runs of digits compared as numbers, so "Lecture 2" comes before
    "Lecture 10", and the rest without regard to case."""
    parts = re.split(r"(\d+)", str(name)[:500].casefold())
    return [int(p) if i % 2 else p for i, p in enumerate(parts)]


def list_folder(fid, key=None):
    """A shared folder's contents, from its page (_folder_page, FolderPage): {"name", "files" ([(name, id, MIME
    type)] of its audio and video files, in natural name order), "folders" (how many subfolders), "others" (how many
    other files)}. A subfolder is known by its link (…/drive/folders/<id>) or its icon; a file by its link
    (…/file/d/<id>/view), and anything else, a Google Docs file among them, is another file."""
    page = FolderPage()
    page.feed(_folder_page(fid, key))
    page.close()
    if not page.listed:
        raise DriveError(NOT_LISTED)
    files, folders, others, seen = [], 0, 0, set()
    for e in page.entries:
        try:
            kind, eid = parse(e["href"])
        except DriveError:
            kind, eid = None, ""
        name = " ".join(e["name"].split()) or eid
        if kind == "folder" or e["folder"]:
            folders += 1
        elif kind == "file" and is_media(name, e["type"]):
            if eid not in seen:  # (the same file twice is listed once)
                seen.add(eid)
                files.append((name, eid, e["type"]))
        else:
            others += 1
    files.sort(key=lambda f: (natural_key(f[0]), f[0]))
    return {"name": " ".join(page.title.split()), "files": files, "folders": folders, "others": others}


def skipped_words(skipped):
    """What a folder's listing left out, in words: "2 subfolders and 1 other file", or ""."""
    n, m = skipped.get("folders") or 0, skipped.get("files") or 0
    parts = [f"{n} subfolder{'' if n == 1 else 's'}" if n else "", f"{m} other file{'' if m == 1 else 's'}" if m else ""]
    return " and ".join(p for p in parts if p)


def inspect_folder(url):
    """A folder link's audio and video files, in the shape of a YouTube playlist's (app/youtube.py's inspect): {"site"
    ("drive"), "kind" ("folder"), "id", "title" (the folder's name), "url", "count", "duration" (None: the view has
    no lengths), "entries" (each file numbered in natural name order, titled after its name without the extension),
    "skipped" ({"folders", "files"}: the subfolders and the other files), "cut" (more than MAX_FILES were found)}."""
    fid, key = folder_link(url)
    found = list_folder(fid, key)
    skipped = {"folders": found["folders"], "files": found["others"]}
    if not found["files"]:
        words = skipped_words(skipped)
        raise DriveError(f"this folder has no audio or video files ({words} skipped)" if words else "this folder is empty")
    files = found["files"][:MAX_FILES]
    entries = [{"id": eid, "name": name, "title": title_of(name), "index": i, "duration": None, "url": file_url(eid),
                "available": True, "live": False, "captions": None, "use": None, "type": mime}
               for i, (name, eid, mime) in enumerate(files, 1)]
    return {"site": "drive", "kind": "folder", "id": fid, "title": found["name"] or "Google Drive folder",
            "name": found["name"], "url": folder_url(fid), "count": len(entries), "duration": None, "entries": entries,
            "skipped": skipped, "cut": len(found["files"]) > MAX_FILES}


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

    It stops, and deletes what came, as soon as the file is too large. When Drive gives the file's size, at the first
    block: if that size is over the upload limit (cfg.max_upload), or would leave less than MARGIN free beside the
    sound made from it. Whether or not Drive gave a size, as the bytes come: once more than the upload limit has come
    (a size Drive gave can be wrong), or when the free space, checked each CHECK_EVERY bytes (from the start when
    there is no size), is less than MARGIN beside the sound made from what came. It also stops, keeping nothing,
    when the download's answer comes from another site than Drive's (_only_from_drive)."""
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
            if total and not state["sized"]:  # Drive says how large it is: the whole file, as the download starts
                state["sized"] = True
                _room(cfg, folder, total, total - done)
                state["next"] = done + CHECK_EVERY
            if done > cfg.max_upload:  # what has come, whatever size Drive said
                _room(cfg, folder, done, 0)
            if done >= state["next"]:  # and the free space as it comes
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
