"""OpenAI, Groq and Mistral: hosted models behind OpenAI-style /audio/transcriptions endpoints.

The upload is the job's audio encoded with PyAV as Opus in WebM (32 kbps, about 14 MB an hour)
rather than the 16 kHz FLAC (about 50 MB an hour). A recording longer than one request may be is cut
at pauses into parts that fit the service's file size and duration limits, and each part's lines get
the part's start time added. OpenAI's gpt-transcribe and gpt-4o models return text without
timestamps, so they get one request per stretch of at most 25 s instead, and each stretch is a line.
Groq (and OpenAI with diarize_model = "") gives no speaker labels; when labels are asked for, they
come from the speaker model on this computer (Settings → Models), as for the local models.
Importing this module adds the three services to engines.HOSTED.
"""
import base64
import json
import os
import re
import time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

from . import engines
from .config import ROOT, replace_file
from .engines import Cancelled, EngineError, Multipart, split_terms, upload_progress

SR = 16000
FPS = 50            # loudness frames per second: pauses are looked for in 20 ms steps
QUIET = 1e-6        # mean power of -60 dBFS: a stretch that never gets louder than this isn't sent
# upload_format: (PyAV container, encoder, sample format, content type); "flac" is sent as it is
FORMATS = {"webm": ("webm", "libopus", "s16", "audio/webm"), "ogg": ("ogg", "libopus", "s16", "audio/ogg"),
           "mp3": ("mp3", "libmp3lame", "s16p", "audio/mpeg"), "flac": (None, None, None, "audio/flac")}


# ---------------------------------------------------------------------------------------------
# Cutting the recording at pauses, and encoding the pieces
# ---------------------------------------------------------------------------------------------

def loudness(path):
    """Mean power of every 20 ms of the job's audio (read a minute at a time), and its length in s."""
    step, out = SR // FPS, []
    with sf.SoundFile(str(path)) as f:
        seconds = f.frames / f.samplerate
        for block in f.blocks(blocksize=SR * 60, dtype="float32", always_2d=True):
            x = block.mean(axis=1)
            x = np.pad(x, (0, -len(x) % step))
            out.append((x.reshape(-1, step) ** 2).mean(axis=1))
    return (np.concatenate(out) if out else np.zeros(0)), seconds


def quietest(power, lo, hi, width=15):
    """The frame in the middle of the quietest 300 ms between frames lo and hi: a pause to cut at."""
    seg = power[lo:hi]
    if len(seg) <= width:
        return (lo + hi) // 2
    return lo + int(np.argmin(np.convolve(seg, np.ones(width), "valid"))) + width // 2


def spans(power, seconds, most, window):
    """(start, end) in seconds covering the whole recording, each at most `most` long and ending at
    the quietest moment of its last `window` seconds."""
    out, a, top, win = [], 0, int(most * FPS), max(1, int(window * FPS))
    while len(power) - a > top:
        cut = quietest(power, a + top - win, a + top)
        out.append((a / FPS, cut / FPS))
        a = cut
    out.append((a / FPS, seconds))
    return [(round(x, 2), round(y, 2)) for x, y in out]


def silent(power, a, b):
    """True when no 20 ms of a..b (seconds) is louder than -60 dBFS: nothing to transcribe."""
    seg = power[int(a * FPS):int(b * FPS) + 1]
    return not len(seg) or float(seg.max()) < QUIET


def halves(power, a, b):
    """Seconds a..b cut in two at the quietest moment of its middle third."""
    third = (b - a) / 3
    cut = round(quietest(power, int((a + third) * FPS), int((b - third) * FPS)) / FPS, 2)
    return [(a, cut), (cut, b)]


def part_seconds(model, audio, fmt, kbps):
    """The longest part: max_part_s, and what fits in max_part_mb at the upload's bitrate (for FLAC,
    this recording's own bitrate plus a margin)."""
    if fmt == "flac":
        kbps = 1.3 * audio.stat().st_size * 8 / 1000 / max(1.0, sf.info(str(audio)).duration)
    fits = float(model.get("max_part_mb", 24)) * 8000 / kbps * 0.9
    return max(10.0, min(float(model.get("max_part_s", 3600)), fits))


def read_blocks(f, n, block=SR * 10):
    """n int16 samples from an open sound file, ten seconds at a time."""
    while n > 0:
        x = f.read(min(n, block), dtype="int16")
        if not len(x):
            return
        n -= len(x)
        yield x


def encode(src, a, b, fmt, kbps, dest):
    """Seconds a..b of the job's audio as an upload file: Opus or MP3 encoded with PyAV, or FLAC."""
    container, codec, sample_format, _ = FORMATS[fmt]
    with sf.SoundFile(str(src)) as f:
        f.seek(int(a * SR))
        n = int(b * SR) - int(a * SR)
        if container is None:
            with sf.SoundFile(str(dest), "w", samplerate=SR, channels=1, subtype="PCM_16", format="FLAC") as out:
                for x in read_blocks(f, n):
                    out.write(x)
            return
        import av  # the FFmpeg libraries that come with faster-whisper, Opus and MP3 encoders included
        with av.open(str(dest), "w", format=container) as out:
            stream = out.add_stream(codec, rate=SR, layout="mono")
            stream.bit_rate = int(kbps * 1000)
            pts = 0
            for x in read_blocks(f, n):
                frame = av.AudioFrame.from_ndarray(x.reshape(1, -1), format=sample_format, layout="mono")
                frame.sample_rate, frame.pts = SR, pts
                pts += len(x)
                for packet in stream.encode(frame):
                    out.mux(packet)
            for packet in stream.encode(None):
                out.mux(packet)


# ---------------------------------------------------------------------------------------------
# Requests: one upload per part, tried again after a short rate limit or a server error
# ---------------------------------------------------------------------------------------------

def pause(seconds, cancelled):
    """Sleep, but notice a cancel within half a second."""
    end = time.time() + seconds
    while True:
        if cancelled():
            raise Cancelled()
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(0.5, left))


def retry_delay(r, attempt):
    """Seconds to wait before sending again, or None when trying again wouldn't help."""
    if r.status_code == 429:  # a rate limit says when it resets; a used-up quota doesn't
        try:
            seconds = float(r.headers.get("Retry-After", ""))
        except ValueError:
            return None
        return seconds if 0 <= seconds <= 60 else None
    return (2, 10, 30)[min(attempt, 2)] if r.status_code in (500, 502, 503, 504) else None


def check(r, service):
    """engines.check, plus the reason Mistral gives as {"message": ...} rather than in "error"/"detail"."""
    try:
        engines.check(r, service)
    except EngineError as e:
        reason = message_of(r)
        if reason and reason not in str(e):
            raise EngineError(f"{e}; {reason}") from None
        raise


def message_of(r):
    try:
        body = r.json()
    except ValueError:
        return ""
    if not isinstance(body, dict) or body.get("error") or body.get("detail"):
        return ""  # engines.check has read these already
    msg = body.get("message")
    if isinstance(msg, dict):  # validation errors: {"message": {"detail": [{"msg": ...}, ...]}}
        msg = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in msg.get("detail") or [])
    return str(msg or "").strip()


def post(session, url, headers, fields, path, content_type, on_read, cancelled, service, tries=5):
    """Upload one file with its form fields and return the JSON reply. A 429 that says when to come
    back (within a minute), a server error or a dropped connection is tried again, five times in all
    (a long job can meet a per-minute limit more than once)."""
    for attempt in range(tries):
        last = attempt == tries - 1
        body = Multipart(fields, "file", path, Path(path).name, content_type, on_read, cancelled)
        try:
            r = session.post(url, data=body, headers={**headers, "Content-Type": body.content_type},
                             timeout=(30, 3 * 3600))
        except (requests.ConnectionError, requests.Timeout) as e:
            if last:
                raise EngineError(f"{service}: the connection failed ({type(e).__name__})") from None
            pause(5, cancelled)
            continue
        finally:
            body.close()
        delay = retry_delay(r, attempt)
        if delay is None or last:
            break
        pause(delay, cancelled)
    check(r, service)
    try:
        data = r.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise EngineError(f"{service}: the reply was not a transcript (HTTP {r.status_code})")
    return data


def at_output_limit(model, reply):
    """True when a reply used all of max_output_tokens (the 4o models stop at 2,000)."""
    cap, used = model.get("max_output_tokens"), (reply.get("usage") or {}).get("output_tokens")
    return bool(cap and isinstance(used, (int, float)) and used >= int(cap) - 5)


def reader(on_progress, service, single):
    """Upload progress for a job sent in one piece, then "remote" once all of it is sent. A job in
    several pieces reports which piece it is on instead."""
    if not single:
        return lambda sent, total: None
    report = upload_progress(on_progress, service)

    def on_read(sent, total):
        report(sent, total)
        if sent == total:
            on_progress({"stage": "remote", "service": service})
    return on_read


def save(job_dir, lines, replies):
    """The lines so far where the app looks for a running job's transcript, and the raw replies."""
    for path, data in ((job_dir / "engine" / "lines.json", lines), (job_dir / "hosted.json", replies)):
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        replace_file(tmp, path)


def run(cfg, model, job_dir, on_progress, cancelled, fields, parse, piece_s=None, timeline=None):
    """Send the job's audio in as many requests as it needs and join the lines. fields(i, a, b) gives
    the form fields of the request for seconds a..b, parse(reply, i, a, b) its lines. With a
    timeline from the voiceprints, every line gets the speaker heard most during it."""
    job_dir = Path(job_dir)
    audio, service = job_dir / "audio.flac", model["service"]
    fmt, kbps = model.get("upload_format", "webm"), float(model.get("upload_kbps", 32))
    power, seconds = loudness(audio)
    if piece_s:  # one line each, for models without timestamps: 10 to 25 s long with piece_s = 25
        todo = spans(power, seconds, piece_s, piece_s * 0.6)
    else:
        most = part_seconds(model, audio, fmt, kbps)
        todo = spans(power, seconds, most, min(120.0, most / 4))
    url = f"{model['base_url'].rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {cfg.api_key(model)}"}
    upload, limit = job_dir / f"upload.{fmt}", float(model.get("max_part_mb", 24)) * 1e6
    lines, replies, i = [], [], 0
    with requests.Session() as session:
        while i < len(todo):
            if cancelled():
                raise Cancelled()
            a, b = todo[i]
            if b - a < 0.1 or silent(power, a, b):
                i += 1
                continue
            if len(todo) > 1:
                on_progress({"stage": "transcribing", "done": i, "total": len(todo), "service": service})
            try:
                encode(audio, a, b, fmt, kbps, upload)
                if upload.stat().st_size > limit and b - a >= 2:  # larger than planned: cut it in two
                    todo[i:i + 1] = halves(power, a, b)
                    continue
                reply = post(session, url, headers, fields(i, a, b), upload, FORMATS[fmt][3],
                             reader(on_progress, service, len(todo) == 1), cancelled, service)
            finally:
                upload.unlink(missing_ok=True)
            if at_output_limit(model, reply) and b - a >= 20:  # the text may stop early: send it in halves
                todo[i:i + 1] = halves(power, a, b)
                continue
            replies.append({"start": a, "end": b, "reply": reply})
            new = parse(reply, i, a, b)
            if timeline:
                for x in new:
                    x["speaker"] = timeline.speaker(x["start"], x["end"])
            lines += new
            save(job_dir, lines, replies)
            i += 1
    return lines, replies


# ---------------------------------------------------------------------------------------------
# Speaker labels from the speaker model on this computer, for the models that have none
# ---------------------------------------------------------------------------------------------

class Timeline:
    """Who is heard when: the voiceprint windows' centres (s) and speakers from the voice step, and the name of the
    speaker model's file or folder (recorded in the job, as transcribe.py records it for local runs)."""

    def __init__(self, centers, labels, model=None):
        self.centers, self.labels = np.asarray(centers, dtype=float), np.asarray(labels, dtype=int)
        self.model = model

    def speaker(self, start, end):
        """The speaker of most windows centred between start and end, else of the window nearest the
        middle (a line too short to hold one); None when there are no windows at all."""
        if not len(self.centers):
            return None
        i, j = np.searchsorted(self.centers, [start, end])
        if j > i:
            return str(int(np.argmax(np.bincount(self.labels[i:j]))))
        return str(int(self.labels[np.argmin(np.abs(self.centers - (start + end) / 2))]))


def voice_timeline(cfg, job_dir, opts, on_progress, cancelled):
    """With speaker labels on and a speaker model downloaded: the voice step the local models use
    (speakers.py) with the speaker model in use, run in the model process (app/worker.py
    --speaker-timeline). Else None."""
    args = cfg.speaker_args(opts.get("speakers", "none"))  # --speakers N --voiceprint-model PATH, or []
    if not args:
        return None
    on_progress({"stage": "speakers"})
    job_dir = Path(job_dir)
    log_path = job_dir / "log.txt"
    cmd = engines.worker_command(cfg) + [
        "--speaker-timeline", str(job_dir / "audio.flac"), *args,
        "--threads", str(min(4, int(cfg.setting("threads"))))]  # as transcribe.py's voice step
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(ROOT), "SEDJEM_LOG": str(log_path)}
    with open(log_path, "a", encoding="utf-8") as log:
        proc = engines.spawn(cmd, log, env)
        while proc.poll() is None:
            if cancelled():
                engines.stop(proc)
                raise Cancelled()
            time.sleep(0.25)
    out = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = next((x for x in reversed(out) if x.startswith('{"centers"')), None)
    if proc.returncode != 0 or found is None:
        tail = [x for x in out if x.strip()][-8:]
        raise EngineError("\n".join(["telling the speakers apart by voice failed", *tail]))
    data = json.loads(found)
    return Timeline(data["centers"], data["labels"], model=Path(args[args.index("--voiceprint-model") + 1]).name)


# ---------------------------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------------------------

class Voices:
    """Speaker numbers across parts. A part's own labels (A, B, ... or speaker_1, ...) get new
    numbers, except the names that part was sent as known speakers, which keep their number."""

    def __init__(self):
        self.numbers, self.talk, self.longest = {}, {}, {}

    def number(self, part, label, known):
        if label in (None, ""):
            return None
        if label in known:
            return known[label]
        return self.numbers.setdefault((part, label), str(len(self.numbers) + 1))

    def heard(self, number, start, end):
        self.talk[number] = self.talk.get(number, 0) + end - start
        s, e = self.longest.get(number, (0, 0))
        if end - start > e - s:
            self.longest[number] = (start, end)

    def references(self, most=4, longest=8.0, shortest=3.0):
        """(name, number, start, end) for the speakers heard most so far: the middle 3 to 8 s of each
        one's longest segment (OpenAI takes up to 4 known speakers, with clips of 2 to 10 s)."""
        out = []
        for n in sorted(self.talk, key=self.talk.get, reverse=True):
            s, e = self.longest[n]
            length = min(longest, e - s - 0.5)
            if length >= shortest:
                mid = (s + e) / 2
                out.append((f"Speaker {n}", n, round(mid - length / 2, 2), round(mid + length / 2, 2)))
            if len(out) == most:
                break
        return out


def text_lines(data, part, a, b):
    """A reply with text only: one line for the whole stretch."""
    text = (data.get("text") or "").strip()
    return [{"start": round(a, 2), "end": round(b, 2), "speaker": None, "text": text}] if text else []


def segment_lines(data, part, a, b, voices=None, known=None):
    """Lines from the segments of a verbose_json, diarized_json or Voxtral reply for seconds a..b of
    the recording (every time gets the part's start added)."""
    if not data.get("segments"):
        return text_lines(data, part, a, b)
    lines, t = [], a
    for s in data["segments"]:
        text = (s.get("text") or "").strip()
        if not text or (s.get("no_speech_prob", 0) > 0.6 and s.get("avg_logprob", 0) < -1):
            continue  # Whisper's own test for a segment that is really silence
        start = min(b, a + float(s["start"])) if s.get("start") is not None else t
        end = min(b, a + float(s["end"])) if s.get("end") is not None else start
        t = end = max(start, end)
        speaker = voices.number(part, s.get("speaker", s.get("speaker_id")), known or {}) if voices else None
        if speaker:
            voices.heard(speaker, start, end)
        lines.append({"start": round(start, 2), "end": round(end, 2), "speaker": speaker, "text": text})
    return lines


def detected_language(replies):
    for x in replies:
        data = x["reply"]
        if isinstance(data.get("language"), str) and data["language"]:
            return data["language"]
        codes = [y.get("code") for y in data.get("languages") or [] if isinstance(y, dict) and y.get("code")]
        if codes:  # gpt-transcribe: [{"code": "ar"}, ...]
            return ", ".join(codes)
    return None


def result(lines, replies, name, timeline=None):
    meta = {"detected_language": detected_language(replies), "api_model": name, "parts": len(replies)}
    if timeline is not None:
        meta["voiceprint_model"] = timeline.model  # the speaker model's file or folder
    return {"lines": lines, "meta": meta}


# ---------------------------------------------------------------------------------------------
# The three services
# ---------------------------------------------------------------------------------------------

def language(opts):
    """The language option as the `language` field; auto-detect sends none."""
    lang = opts.get("language", "ar")
    return [("language", lang)] if lang in ("ar", "en") else []


def whisper_prompt(opts, room=200):
    """For stock Whisper: the Egyptian style hint (it took local large-v3 from 47% to 34% WER on ArzEn),
    except for English-only runs, then the user's terms, as many as fit in about 200 characters
    (Whisper prompts are limited to 224 tokens)."""
    terms, used = [], 0
    for t in split_terms(opts.get("prompt")):
        used += len(t) + 2
        if used > room:
            break
        terms.append(t)
    head = []
    if opts.get("language", "ar") != "en":
        from transcribe import STYLE_PROMPT
        head = [STYLE_PROMPT]
    text = " ".join(head + ([", ".join(terms)] if terms else []))
    return [("prompt", text)] if text else []


def openai(cfg, model, job_dir, job, on_progress, cancelled):
    """OpenAI's transcription models. Speaker labels come from gpt-4o-transcribe-diarize (see
    openai_diarized), or with diarize_model = "" from the speaker model on this computer. Of the others,
    whisper-1 returns segments with times; gpt-transcribe and the gpt-4o models return text only, so
    they are sent one short stretch at a time."""
    opts = job.get("options") or {}
    if opts.get("speakers", "none") != "none" and model.get("diarize_model"):
        return openai_diarized(cfg, model, job_dir, opts, on_progress, cancelled)
    name, terms = model.get("api_model", "gpt-transcribe"), split_terms(opts.get("prompt"))
    timeline = voice_timeline(cfg, job_dir, opts, on_progress, cancelled)
    if "whisper" in name:
        fields = [("model", name), ("response_format", "verbose_json"), ("timestamp_granularities[]", "segment"),
                  *language(opts), *whisper_prompt(opts)]
        lines, replies = run(cfg, model, job_dir, on_progress, cancelled, lambda *_: fields, segment_lines,
                             timeline=timeline)
        return result(lines, replies, name, timeline)
    fields = [("model", name), ("response_format", "json")]
    if name.startswith("gpt-transcribe"):  # several languages, and the terms as keywords
        langs = {"ar": ["ar", "en"], "en": ["en"]}.get(opts.get("language", "ar"), [])
        fields += [("languages[]", x) for x in langs]
        fields += [("keywords[]", t) for t in terms if not re.search(r"[<>]", t)][:100]
    else:
        fields += language(opts) + ([("prompt", ", ".join(terms)[:1000])] if terms else [])
    lines, replies = run(cfg, model, job_dir, on_progress, cancelled, lambda *_: fields, text_lines,
                         piece_s=float(model.get("piece_s", 25)), timeline=timeline)
    return result(lines, replies, name, timeline)


def openai_diarized(cfg, model, job_dir, opts, on_progress, cancelled):
    """gpt-4o-transcribe-diarize (no prompt). Each part after the first is sent a clip of each of the
    (up to 4) voices heard most so far as a known speaker, so those keep their number across parts."""
    name, voices, known = model["diarize_model"], Voices(), {}
    fmt, kbps = model.get("upload_format", "webm"), float(model.get("upload_kbps", 32))
    audio, clip = Path(job_dir) / "audio.flac", Path(job_dir) / f"voice.{fmt}"

    def fields(part, a, b):
        refs = voices.references() if model.get("carry_speakers", True) else []
        known[part] = {label: n for label, n, _, _ in refs}
        urls = []
        try:
            for _, _, s, e in refs:
                encode(audio, s, e, fmt, kbps, clip)
                urls.append(f"data:{FORMATS[fmt][3]};base64,{base64.b64encode(clip.read_bytes()).decode()}")
        finally:
            clip.unlink(missing_ok=True)
        return ([("model", name), ("response_format", "diarized_json"), ("chunking_strategy", "auto"),
                 *language(opts)] + [("known_speaker_names[]", x[0]) for x in refs]
                + [("known_speaker_references[]", u) for u in urls])

    def parse(data, part, a, b):
        return segment_lines(data, part, a, b, voices, known.get(part))

    return result(*run(cfg, model, job_dir, on_progress, cancelled, fields, parse), name)


def groq(cfg, model, job_dir, job, on_progress, cancelled):
    """Groq's hosted Whisper: segments with times. It has no speaker labels, so they come from the
    speaker model on this computer."""
    opts = job.get("options") or {}
    name = model.get("api_model", "whisper-large-v3")
    fields = [("model", name), ("response_format", "verbose_json"), ("timestamp_granularities[]", "segment"),
              *language(opts), *whisper_prompt(opts)]
    timeline = voice_timeline(cfg, job_dir, opts, on_progress, cancelled)
    lines, replies = run(cfg, model, job_dir, on_progress, cancelled, lambda *_: fields, segment_lines,
                         timeline=timeline)
    return result(lines, replies, name, timeline)


def mistral(cfg, model, job_dir, job, on_progress, cancelled):
    """Mistral's Voxtral: segments with times and, with diarize, speaker ids. Its docs say timestamps
    can't be combined with a language, so the language is always detected."""
    opts = job.get("options") or {}
    name, voices = model.get("api_model", "voxtral-mini-2602"), None
    fields = [("model", name), ("timestamp_granularities", "segment")]
    if opts.get("speakers", "none") != "none":
        fields.append(("diarize", "true"))
        voices = Voices()
    # Context bias takes up to 100 terms. The docs write phrases with underscores and the reply keeps
    # them, so phrases are sent joined and get their spaces back in the text.
    phrases = {re.sub(r"\s+", "_", t): t for t in split_terms(opts.get("prompt"))[:100]}
    fields += [("context_bias", x) for x in phrases]

    def parse(data, part, a, b):
        lines = segment_lines(data, part, a, b, voices)
        for x in lines:
            for joined, t in phrases.items():
                if joined != t:
                    x["text"] = x["text"].replace(joined, t)
        return lines

    return result(*run(cfg, model, job_dir, on_progress, cancelled, lambda *_: fields, parse), name)


engines.HOSTED.update(openai=openai, groq=groq, mistral=mistral)
