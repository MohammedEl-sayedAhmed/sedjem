"""YouTube links: a video, or a playlist (a course), downloaded with yt-dlp to transcribe and to play beside
the transcript.

inspect() reads a link: a video, or a playlist and its videos in order, with the languages of the captions
people made for each (yt-dlp's "subtitles"; YouTube's automatic captions are never used). download() fetches
one video for a job: the best H.264 video stream up to 720 pixels high and the best AAC sound, joined into
video.mp4 with PyAV (the streams copied, the index at the start so a browser can play it while it loads).
Without such a pair it takes a single MP4 that has both, and without any H.264 version only the sound
(source.<ext>), so the video can still be transcribed. No ffmpeg program is needed. When the video has
captions made by people in its own language (Arabic or English), they are saved as captions.vtt and the job
uses them as its transcript instead of a speech model (caption_lines).

YouTube hides the addresses of its streams behind a JavaScript challenge. yt-dlp solves it with the
yt-dlp-ejs scripts in a JavaScript runtime: the Deno that Settings can download (pinned like the models),
else Deno, Node.js or QuickJS found on this computer. Without one, yt-dlp falls back to what YouTube serves
without the challenge, which works for many videos for now but may miss formats; the app says so.
"""
import heapq
import html
import re
import shutil
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path

from .config import replace_file
from .engines import Cancelled
from .transcript import MAX_LINE

HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
         "youtube-nocookie.com", "www.youtube-nocookie.com"}
STREAM_HOSTS = (".googlevideo.com", ".youtube.com")  # where YouTube's streams and captions are served from
EXTRACTORS = ("youtube", "youtube:tab", "youtube:playlist")  # yt-dlp's only: its generic one never runs
EXTRACTOR_KEYS = {"Youtube", "YoutubeTab", "YoutubePlaylist"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
LIST_ID = re.compile(r"[A-Za-z0-9_-]{2,64}")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{6,64}")  # what a job's youtube_id must look like (it names files)
NOT_YOUTUBE = "paste the link of a YouTube video or playlist"
MAX_HEIGHT = 720
LANGS = {"ar": "Arabic", "en": "English"}
CAPTIONS_MODEL, CAPTIONS_TITLE = "youtube-captions", "YouTube captions (made by people)"
GONE = {"[Private video]", "[Deleted video]", "[Unavailable video]"}
MAX_ENTRIES = 300                  # a longer playlist is cut here
CHECK_WORKERS, CHECK_SECONDS = 4, 30  # the caption check of a playlist's videos: in parallel, for at most this long


class YouTubeError(Exception):
    pass


def watch_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def check_url(url):
    """A YouTube link rebuilt from its parts, so nothing else of it reaches yt-dlp: the video's
    https://www.youtube.com/watch?v=<id> (with &list=<id> when it names a playlist too), or the playlist's
    https://www.youtube.com/playlist?list=<id>. Raises YouTubeError for anything else: another site, a host
    that only looks like YouTube's, a user name, password or odd port in the address, a channel."""
    url = str(url or "").strip()
    if url and not re.match(r"(?i)[a-z][a-z0-9+.-]*://", url):
        url = "https://" + url
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port
    except ValueError:
        raise YouTubeError(NOT_YOUTUBE)
    host = (p.hostname or "").lower()
    if p.scheme.lower() not in ("http", "https") or host not in HOSTS or "@" in p.netloc or port not in (None, 80, 443):
        raise YouTubeError(NOT_YOUTUBE)
    query = urllib.parse.parse_qs(p.query)
    first = lambda key: (query.get(key) or [""])[0]
    parts = [x for x in p.path.split("/") if x]
    video, playlist = None, first("list") or None
    if host == "youtu.be":
        video = parts[0] if len(parts) == 1 else ""
    elif parts == ["watch"]:
        video = first("v")
    elif len(parts) == 2 and parts[0] in ("shorts", "live", "embed", "v", "e"):
        video = parts[1]
    elif parts and (parts[0].startswith("@") or parts[0] in ("channel", "c", "user")):
        raise YouTubeError("this is a channel; paste the link of one of its playlists or videos")
    elif parts != ["playlist"]:
        raise YouTubeError(NOT_YOUTUBE)
    if video is not None and not VIDEO_ID.fullmatch(video) or playlist and not LIST_ID.fullmatch(playlist):
        raise YouTubeError(NOT_YOUTUBE)
    if video and playlist:
        return f"https://www.youtube.com/watch?v={video}&list={playlist}"
    if video:
        return watch_url(video)
    if playlist:
        return f"https://www.youtube.com/playlist?list={playlist}"
    raise YouTubeError(NOT_YOUTUBE)


def on_youtube(url, hosts=HOSTS):
    """Whether an address yt-dlp gave back is still YouTube's (hosts, or names ending in one of them)."""
    try:
        p = urllib.parse.urlsplit(str(url or ""))
        host = (p.hostname or "").lower()
        ok_port = p.port in (None, 80, 443)
    except ValueError:
        return False
    named = host in hosts if isinstance(hosts, set) else host.endswith(hosts)
    return p.scheme in ("http", "https") and named and "@" not in p.netloc and ok_port


def clean_error(e):
    """yt-dlp's message without its prefixes and command-line advice: "Video unavailable. This video is private"."""
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(getattr(e, "msg", None) or e)).strip()
    msg = re.sub(r"^(ERROR:\s*)?(\[[\w:]+\]\s*)?([\w-]{6,}:\s+)?", "", msg)
    msg = re.split(r"\.?\s+(Use --|See https://github\.com/yt-dlp)", msg)[0].strip()
    return msg or type(e).__name__


# ---- the JavaScript runtime ------------------------------------------------------------------------

def deno_path(cfg):
    """The Deno binary Settings downloaded (unpacked next to its package, as app/downloads.py does), or None."""
    item = cfg.download_items().get("deno")
    if not item:
        return None
    f = item["files"][0]
    member = next(iter(f.get("unpack") or {}), None)
    path = cfg.path(f["path"]).parent / member.rsplit("/", 1)[-1] if member else cfg.path(f["path"])
    return path if path.is_file() else None


def js_runtimes(cfg):
    """yt-dlp's js_runtimes: every runtime it can use safely (Bun runs code without a sandbox, so it is left
    out); yt-dlp picks the best one it finds."""
    deno = deno_path(cfg)
    return {"deno": {"path": str(deno)} if deno else {}, "node": {}, "quickjs": {}}


_runtime = {}
_runtime_lock = threading.Lock()


def runtime(cfg):
    """The JavaScript runtime yt-dlp will use here: {"name", "version", "downloaded"}, or None. Looked up once
    (it starts each runtime to ask its version), again after Deno was downloaded or removed."""
    deno = deno_path(cfg)
    key = str(deno)
    with _runtime_lock:
        if key not in _runtime:
            _runtime.clear()
            _runtime[key] = _find_runtime(deno)
        return _runtime[key]


def _find_runtime(deno):
    try:
        from yt_dlp.utils._jsruntime import DenoJsRuntime, NodeJsRuntime, QuickJsRuntime
    except ImportError:
        return None
    for name, cls, path in (("Deno", DenoJsRuntime, deno), ("Node.js", NodeJsRuntime, None),
                            ("QuickJS", QuickJsRuntime, None)):
        try:
            info = cls(path=str(path) if path else None).info
        except Exception:  # noqa: BLE001 — a runtime that can't be started counts as missing
            info = None
        if info and info.supported:
            return {"name": name, "version": info.version, "downloaded": bool(path)}
    return None


# ---- yt-dlp ----------------------------------------------------------------------------------------

class _Log:
    """yt-dlp's messages, kept instead of printed (the app has no console)."""

    def __init__(self):
        self.warnings = []

    def debug(self, msg):
        pass

    info = debug

    def warning(self, msg):
        if not re.search(r"JS runtime|JavaScript runtime", str(msg)):  # the app says that in its own words
            self.warnings.append(clean_error(msg))

    def error(self, msg):
        pass


def _ydl(cfg, **extra):
    """A yt-dlp instance for one task (they are not shared between threads)."""
    import yt_dlp
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True, "logger": _Log(),
                             "js_runtimes": js_runtimes(cfg), "socket_timeout": 30, "retries": 10,
                             "extract_flat": "in_playlist", "allowed_extractors": [re.escape(x) for x in EXTRACTORS],
                             **extra})


def _extract(ydl, url, hops=3):
    """What yt-dlp reads from a checked link (check_url), refused unless one of YouTube's own extractors read
    it from a YouTube page. A link it answers with another link is followed only if that is YouTube's too."""
    try:
        info = ydl.extract_info(check_url(url), download=False, process=False)
    except (Cancelled, YouTubeError):
        raise
    except Exception as e:  # yt-dlp's DownloadError and ExtractorError carry a message worth showing
        raise YouTubeError(clean_error(e))
    if info.get("_type") in ("url", "url_transparent") and hops:
        return _extract(ydl, info.get("url"), hops - 1)
    if info.get("extractor_key") not in EXTRACTOR_KEYS or not on_youtube(info.get("webpage_url")):
        raise YouTubeError("the link did not lead to a YouTube video or playlist")
    return info


# ---- reading a link ---------------------------------------------------------------------------------

def human_captions(info):
    """{language code: track name} of the captions people made in Arabic or English (never YouTube's
    automatic ones), with a WebVTT version."""
    out = {}
    for code, tracks in (info.get("subtitles") or {}).items():
        if code.split("-")[0].lower() in LANGS and any(t.get("ext") == "vtt" and on_youtube(t.get("url"), STREAM_HOSTS)
                                                       for t in tracks or []):
            out[code] = next((t.get("name") for t in tracks if t.get("name")), None) or LANGS[code.split("-")[0].lower()]
    return out


def pick_captions(info, language="ar"):
    """The captions to use as the transcript: the language code, or None. Only in the video's own language
    when YouTube knows it (a translation is not a transcript), else Arabic or English, the job's first."""
    found = human_captions(info)
    base = lambda code: code.split("-")[0].lower()
    own = base(info.get("language") or "")
    order = [own] if own else [language if language in LANGS else "ar", "ar", "en"]
    for want in order:
        codes = sorted((c for c in found if base(c) == want), key=lambda c: (c.lower() != want, len(c), c))
        if codes:
            return codes[0]
    return None


def _captions_facts(info):
    """captions: the languages of the captions people made (Arabic or English); use: the ones a job would
    take as its transcript (pick_captions), or None."""
    return {"captions": sorted(human_captions(info)), "use": pick_captions(info), "language": info.get("language")}


def _youtube_entry(e):
    """Whether a playlist's entry is a YouTube video: YouTube's own extractor, a video id, and an address
    (if it has one) on YouTube. Its address is always made again from the id (watch_url)."""
    return e.get("ie_key") in (None, "Youtube") and bool(VIDEO_ID.fullmatch(str(e.get("id") or ""))) \
        and (not e.get("url") or on_youtube(e["url"]))


def _entry(info, index):
    """What the preview shows about one video. captions and use stay None until the video itself is read
    (a playlist's listing doesn't say)."""
    vid = str(info.get("id") or "")
    title = str(info.get("title") or vid)
    live = info.get("live_status") in ("is_live", "is_upcoming", "post_live")
    ok = _youtube_entry(info)
    out = {"id": vid, "title": title, "duration": info.get("duration"), "index": index,
           "url": watch_url(vid) if ok else None, "available": ok and title not in GONE and not live, "live": live,
           "captions": None, "use": None, "language": info.get("language")}
    if not ok:
        out["problem"] = "Not a YouTube video"
    if "subtitles" in info or "formats" in info:  # a full extraction, not a playlist's listing
        out.update(_captions_facts(info))
    return out


def inspect(cfg, url, check_captions=True):
    """A link's video or playlist: {"kind", "id", "title", "uploader", "url", "count", "duration", "entries",
    "checked", "runtime", "warnings"}. A playlist's videos are listed in order, and their captions looked up
    in parallel for up to CHECK_SECONDS (a video not reached has captions None; its job decides then)."""
    url = check_url(url)
    log = None
    with _ydl(cfg) as ydl:
        log = ydl.params["logger"]
        info = _extract(ydl, url)
        kind = "playlist" if info.get("_type") in ("playlist", "multi_video") or "entries" in info else "video"
        if kind == "video":
            entries = [_entry({**info, "ie_key": "Youtube", "url": None}, 1)]
            if entries[0]["live"]:
                raise YouTubeError("this is a live stream; it can be transcribed once it has ended")
            if not entries[0]["url"]:
                raise YouTubeError(NOT_YOUTUBE)
        else:
            if not LIST_ID.fullmatch(str(info.get("id") or "")):
                raise YouTubeError(NOT_YOUTUBE)
            raw = list(islice(info.get("entries") or [], MAX_ENTRIES))
            if any(e.get("_type") == "playlist" or e.get("ie_key") == "YoutubeTab" for e in raw if isinstance(e, dict)):
                raise YouTubeError("this is a channel; paste the link of one of its playlists or videos")
            entries = [_entry(e, i) for i, e in enumerate((e for e in raw if isinstance(e, dict)), 1)]
            if not entries:
                raise YouTubeError("this playlist has no videos")
    if kind == "playlist" and check_captions:
        _check_captions(cfg, [e for e in entries if e["available"]])
    pid = info.get("id")
    return {"kind": kind, "id": pid, "title": info.get("title") or pid,
            "uploader": info.get("uploader") or info.get("channel"),
            "url": entries[0]["url"] if kind == "video" else f"https://www.youtube.com/playlist?list={pid}",
            "count": len(entries), "duration": sum(e["duration"] or 0 for e in entries if e["available"]),
            "entries": entries, "checked": sum(1 for e in entries if e["captions"] is not None),
            "cut": kind == "playlist" and len(entries) >= MAX_ENTRIES,
            "runtime": runtime(cfg), "warnings": sorted(set(log.warnings if log else []))[:3]}


def _check_captions(cfg, entries):
    deadline = time.time() + CHECK_SECONDS

    def one(e):
        if time.time() > deadline:
            return
        try:
            with _ydl(cfg) as ydl:
                info = _extract(ydl, e["url"])
            e.update(_captions_facts(info), duration=e["duration"] or info.get("duration"))
        except YouTubeError as err:  # private, members only, removed...: shown, and not picked
            e.update(available=False, problem=str(err))

    with ThreadPoolExecutor(CHECK_WORKERS) as pool:
        list(pool.map(one, entries))


# ---- downloading -----------------------------------------------------------------------------------

def _size(f):
    return f.get("filesize") or f.get("filesize_approx") or 0


def pick_formats(formats):
    """(video, audio, both, sound): the best H.264 video-only MP4 stream up to MAX_HEIGHT, the best AAC
    sound-only stream (the original language first, not the "drc" one), the best single MP4 with both, and
    the best sound in any codec (for when there is no H.264 version). Direct downloads come before HLS."""
    ok = [f for f in formats or [] if f.get("url") and not f.get("has_drm") and on_youtube(f["url"], STREAM_HOSTS)
          and str(f.get("protocol") or "https") in ("https", "http", "m3u8_native")]
    h264 = lambda f: str(f.get("vcodec") or "").startswith(("avc1", "h264"))
    aac = lambda f: str(f.get("acodec") or "").startswith(("mp4a", "aac"))
    height = lambda f: f.get("height") or 0
    direct = lambda f: str(f.get("protocol") or "https").startswith("http")
    fits = lambda f: 0 < height(f) <= MAX_HEIGHT and f.get("ext") == "mp4"
    vkey = lambda f: (direct(f), height(f), (f.get("fps") or 30) <= 30, f.get("tbr") or 0)
    akey = lambda f: (direct(f), f.get("language_preference") or 0, "drc" not in str(f.get("format_id")),
                      f.get("abr") or f.get("tbr") or 0)
    video = [f for f in ok if h264(f) and f.get("acodec") == "none" and fits(f)]
    audio = [f for f in ok if aac(f) and f.get("vcodec") == "none" and f.get("ext") in ("m4a", "mp4")]
    both = [f for f in ok if h264(f) and aac(f) and fits(f)]
    sound = [f for f in ok if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best = lambda items, key: max(items, key=key, default=None)
    return best(video, vkey), best(audio, akey), best(both, vkey), best(sound, akey)


def download(cfg, job, folder, on_progress, cancelled):
    """Download a job's video into its folder. Returns the fields the job records: "video" ({"width",
    "height"} of video.mp4, or None when only the sound could be had), "captions" ({"lang", "name"} when
    captions.vtt is to be the transcript), "source_name" and "download_note" (why there is no video).
    on_progress(done, total) counts bytes; cancelled() is checked while it runs."""
    folder = Path(folder)
    with _ydl(cfg) as ydl:
        info = _extract(ydl, job["source_url"])
        if job.get("youtube_id") and info.get("id") != job["youtube_id"]:
            raise YouTubeError("the link led to another video than the one added")
        if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            raise YouTubeError("this is a live stream; it can be transcribed once it has ended")
        video, audio, both, sound = pick_formats(info.get("formats"))
        note = None
        if video and audio:
            plan = [("video", video), ("audio", audio)]
        elif both:
            plan = [("both", both)]
        elif sound:
            plan = [("sound", sound)]
            note = f"YouTube has no H.264 version of this video up to {MAX_HEIGHT}p, so only its sound was downloaded"
        else:
            raise YouTubeError("YouTube offers no sound for this video that can be downloaded"
                               + ("" if runtime(cfg) else " (no JavaScript runtime was found; see Settings)"))
        total = sum(_size(f) for _, f in plan) or None
        if total and shutil.disk_usage(folder).free < total * 2.2 + 1e9:  # the streams, then the joined file
            raise YouTubeError("not enough free disk space for this video")
        base = [0]

        def hook(d):
            if cancelled():
                raise Cancelled()
            if d.get("status") == "downloading":
                got = d.get("downloaded_bytes") or 0
                size = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                on_progress(base[0] + got, max(total or 0, base[0] + size) or None)

        ydl.add_progress_hook(hook)
        files = {}
        for role, f in plan:
            ext = f.get("ext") or "mp4"
            name = folder / (f"source.{ext}" if role == "sound" else f"download-{role}.{ext}")
            try:
                ydl.dl(str(name), {**f, "id": info.get("id"), "title": info.get("title")})
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 — the network, YouTube refusing, the disk
                raise YouTubeError(f"the download failed: {clean_error(e)}")
            if cancelled():
                raise Cancelled()
            if not name.exists():
                raise YouTubeError("the download failed: YouTube sent nothing")
            files[role] = name
            base[0] += name.stat().st_size
        title = re.sub(r'[\\/:*?"<>|]+', " ", job.get("title") or info.get("title") or "video").strip() or "video"
        out = {"video": None, "captions": None, "download_note": note, "source_name": f"{title}.mp4"}
        if "sound" in files:
            out["source_name"] = f"{title}.{files['sound'].suffix.lstrip('.')}"
        else:
            parts = [files["both"]] if "both" in files else [files["video"], files["audio"]]
            try:
                width, height = remux(parts, folder / "video.mp4", cancelled)
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                raise YouTubeError(f"could not join the video and its sound: {e}")
            for p in parts:
                p.unlink(missing_ok=True)
            out["video"] = {"width": width, "height": height}
        if job.get("use_captions", True):
            out["captions"] = save_captions(ydl, info, folder, (job.get("options") or {}).get("language", "ar"))
    return out


def save_captions(ydl, info, folder, language):
    """Fetch the chosen captions made by people as folder/captions.vtt; {"lang", "name"}, or None if there
    are none, or they have no text."""
    code = pick_captions(info, language)
    if not code:
        return None
    track = next(t for t in info["subtitles"][code] if t.get("ext") == "vtt" and on_youtube(t.get("url"), STREAM_HOSTS))
    try:
        text = ydl.urlopen(track["url"]).read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — then the speech model makes the transcript
        return None
    if not caption_lines(parse_vtt(text)):
        return None
    tmp = Path(folder) / "captions.vtt.part"
    tmp.write_text(text, encoding="utf-8")
    replace_file(tmp, Path(folder) / "captions.vtt")
    return {"lang": code, "name": track.get("name") or human_captions(info).get(code) or code}


def remux(parts, dest, cancelled=lambda: False):
    """Join the video and the sound of `parts` (one file with both, or a video file and a sound file) into an
    MP4 at dest, copying the streams, with the index (moov) at the start so it plays while it loads. The
    packets are written in time order across the files. Returns (width, height)."""
    import av
    dest = Path(dest)
    tmp = dest.with_name(dest.stem + ".tmp.mp4")
    inputs = []
    try:
        inputs = [av.open(str(p)) for p in parts]
        vin = next(((c, s) for c in inputs for s in c.streams if s.type == "video"), None)
        ain = next(((c, s) for c in inputs for s in c.streams if s.type == "audio"), None)
        if vin is None:
            raise YouTubeError("the download has no video")
        with av.open(str(tmp), "w", format="mp4", options={"movflags": "+faststart"}) as out:
            wanted = {}  # input file -> (the file, [(its stream, the output stream)])
            for c, s in [vin] + ([ain] if ain else []):
                wanted.setdefault(id(c), (c, []))[1].append((s, out.add_stream_from_template(s)))
            width, height = vin[1].codec_context.width, vin[1].codec_context.height

            def packets(c, pairs):
                to = {s.index: o for s, o in pairs}
                for p in c.demux(*(s for s, _ in pairs)):
                    if p.dts is not None and p.size:  # the empty packet at the end only flushes a decoder
                        yield to[p.stream.index], p

            merged = heapq.merge(*(packets(c, pairs) for c, pairs in wanted.values()),
                                 key=lambda x: float(x[1].dts * x[1].time_base))
            for n, (stream, p) in enumerate(merged):
                if n % 500 == 0 and cancelled():
                    raise Cancelled()
                p.stream = stream
                out.mux(p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        for c in inputs:
            c.close()
    replace_file(tmp, dest)
    return width, height


# ---- captions as a transcript -----------------------------------------------------------------------

_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}")
_TAG = re.compile(r"<[^>]*>")
_SENTENCE_END = re.compile(r"[.!?؟…。][\"'”’»)\]]*$")


def _seconds(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_vtt(text):
    """The cues of a WebVTT file, [{start, end, text}] in time order: tags (<c>, <v Name>, <i>, inline times)
    and direction marks taken out, entities decoded, the lines of a cue joined. Header, NOTE, STYLE and
    REGION blocks, which have no "-->", are skipped."""
    cues = []
    text = str(text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.strip("\n").split("\n")
        at = next((i for i, x in enumerate(lines) if "-->" in x), None)
        m = _CUE.match(lines[at]) if at is not None and at <= 1 else None
        if not m:
            continue
        g = m.groups()
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        words = " ".join(html.unescape(_TAG.sub("", x)) for x in lines[at + 1:])
        words = " ".join(words.replace("‎", " ").replace("‏", " ").split())
        if words:
            cues.append({"start": start, "end": max(start, end), "text": words})
    return sorted(cues, key=lambda c: (c["start"], c["end"]))


def caption_lines(cues, max_gap=1.5, max_line=MAX_LINE):
    """Cues merged into lines of about a sentence: a cue joins the line before it when it follows within
    max_gap seconds, the line doesn't already end a sentence, and the line stays under max_line seconds.
    The same cue repeated is kept once. One speaker, "1"."""
    lines, last_text = [], None
    for c in cues:
        last = lines[-1] if lines else None
        if last and c["text"] == last_text:
            last["end"] = max(last["end"], c["end"])
            continue
        last_text = c["text"]
        if last and c["start"] - last["end"] <= max_gap and c["end"] - last["start"] <= max_line \
                and not _SENTENCE_END.search(last["text"]):
            last["text"] += " " + c["text"]
            last["end"] = max(last["end"], c["end"])
        else:
            lines.append({"start": c["start"], "end": c["end"], "text": c["text"], "speaker": "1"})
    return [{**x, "start": round(x["start"], 2), "end": round(x["end"], 2)} for x in lines]


def captions_transcript(folder):
    """The lines of a job's captions.vtt."""
    return caption_lines(parse_vtt((Path(folder) / "captions.vtt").read_text(encoding="utf-8", errors="replace")))
