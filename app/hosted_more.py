"""More hosted models: Google Gemini, Deepgram, AssemblyAI and Azure AI Speech.

The clients work like the ones in engines.py: each gets on_progress(dict) and cancelled()
callbacks, uploads the job's audio.flac (the app has already asked for confirmation) and returns
{"lines": [...], "meta": {...}}. Importing this module adds them to engines.HOSTED. The requests
follow each provider's documentation as checked on 2026-09-25 (docs/09-app.md); the tests run
them against local stand-ins for the APIs.
"""
import base64
import json
import re
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

from . import engines
from .engines import Cancelled, EngineError, Multipart, split_terms, upload_progress
from .transcript import lines_from_words, relabel


# ---------------------------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------------------------

class FileBody:
    """A file sent as the whole request body, read in blocks so requests streams it with a
    Content-Length (engines.Multipart without the form fields)."""

    def __init__(self, path, on_read, cancelled):
        self.file, self.total = open(path, "rb"), Path(path).stat().st_size
        self.sent, self.on_read, self.cancelled = 0, on_read, cancelled

    def __len__(self):
        return self.total

    def read(self, n=-1):
        if self.cancelled():
            raise Cancelled()
        chunk = self.file.read(self.total if n is None or n < 0 else n)
        self.sent += len(chunk)
        self.on_read(self.sent, self.total)
        return chunk

    def close(self):
        self.file.close()


def check(r, service):
    """engines.check, plus the error text that Deepgram ("err_msg") and Azure ("message") put
    where engines.check doesn't look."""
    try:
        engines.check(r, service)
    except EngineError as e:
        body = {}
        if "json" in r.headers.get("Content-Type", ""):
            try:
                body = r.json()
            except ValueError:
                pass
        extra = (body.get("err_msg") or body.get("message")) if isinstance(body, dict) else None
        if isinstance(extra, str) and extra.strip() and extra.strip() not in str(e):
            raise EngineError(f"{e}; {extra.strip()}") from None
        raise


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


def wait_for(send, cancelled, tick=None):
    """Run send(), a request that returns only when the transcript is ready, in a thread, so a
    cancel is noticed within half a second (the abandoned request then ends on its own).
    tick() is called every 5 s while waiting."""
    box = {}

    def run():
        try:
            box["result"] = send()
        except BaseException as e:  # handed over to the waiting thread
            box["error"] = e
    thread = threading.Thread(target=run, daemon=True, name="sedjem-request")
    thread.start()
    last = time.time()
    while thread.is_alive():
        thread.join(0.5)
        if thread.is_alive() and cancelled():
            raise Cancelled()
        if tick and time.time() - last >= 5:
            last = time.time()
            tick()
    if "error" in box:
        raise box["error"]
    return box["result"]


def upload_then_wait(on_progress, service, waiting):
    """Progress for a request that uploads the audio and then waits for the transcript: once the
    last block is sent, the job shows the waiting state."""
    upload = upload_progress(on_progress, service)

    def on_read(sent, total):
        upload(sent, total)
        if sent >= total:
            on_progress(waiting)
    return on_read


def waiting_state(model, index, count):
    """What a job shows while a service works on part index of count."""
    if count > 1:
        return {"stage": "transcribing", "done": index, "total": count}
    return {"stage": "remote", "service": model["service"]}


def split_at_pauses(path, max_s, out_dir, cancelled, window_s=120):
    """Cut a recording longer than max_s seconds into pieces of at most that length, each cut at
    the quietest half second in the last window_s seconds before the limit, so a cut rarely falls
    inside a word. Returns [(offset_s, path)]: just the file itself when it is short enough."""
    path = Path(path)
    info = sf.info(str(path))
    sr, frames = info.samplerate, info.frames
    if frames <= max_s * sr:
        return [(0.0, path)]
    hop = sr // 10  # loudness in steps of 0.1 s
    energy = []
    with sf.SoundFile(str(path)) as f:
        for block in f.blocks(blocksize=hop * 600, dtype="float32", always_2d=True):
            if cancelled():
                raise Cancelled()
            x = block.mean(axis=1)
            n = len(x) // hop * hop
            if n:
                energy.append((x[:n].reshape(-1, hop) ** 2).mean(axis=1))
    energy = np.concatenate(energy)
    smooth = np.convolve(energy, np.ones(5) / 5, mode="same")  # over half a second
    limit = max(2, int(max_s * sr) // hop)
    window = max(1, min(limit // 2, int(window_s * sr) // hop))  # so no piece is shorter than half the limit
    starts = [0]
    while frames - starts[-1] * hop > max_s * sr:
        lo, hi = starts[-1] + limit - window, starts[-1] + limit
        starts.append(lo + int(np.argmin(smooth[lo:hi])))
    bounds = [s * hop for s in starts] + [frames]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pieces = []
    with sf.SoundFile(str(path)) as src:
        for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
            piece = out_dir / f"part{i + 1}.flac"
            src.seek(a)
            with sf.SoundFile(str(piece), "w", samplerate=sr, channels=src.channels,
                              format="FLAC", subtype="PCM_16") as dst:
                left = b - a
                while left > 0:
                    if cancelled():
                        raise Cancelled()
                    block = src.read(min(left, 60 * sr), dtype="int16")
                    if not len(block):
                        break
                    dst.write(block)
                    left -= len(block)
            pieces.append((a / sr, piece))
    return pieces


def seconds(value, default=0.0):
    """A time given as a number or the way Google's JSON writes durations ("12.340s")."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().removesuffix("s"))
    except ValueError:
        return default


def token(start, end, text, speaker):
    """A word (or phrase) for lines_from_words; a lone punctuation mark joins the one before."""
    return {"start": start, "end": max(start, end), "text": text, "speaker": speaker,
            "glue": all(not c.isalnum() for c in text)}


def parts_lines(parts, tokens_of, speakers=True):
    """Lines from a recording sent in parts: parts is [(offset_s, response)] and tokens_of(response)
    gives its words with the service's speaker ids. Those ids restart in every part, so each part
    keeps its own numbers (the same voice in two parts gets two numbers; merge them in the app)."""
    tokens = []
    for n, (offset, data) in enumerate(parts):
        for t in tokens_of(data):
            who = None if not speakers or t["speaker"] is None else (n, t["speaker"])
            tokens.append({**t, "start": t["start"] + offset, "end": t["end"] + offset, "speaker": who})
    names = relabel(t["speaker"] for t in tokens)
    return lines_from_words([{**t, "speaker": names.get(t["speaker"])} for t in tokens])


def save_raw(job_dir, parts):
    (Path(job_dir) / "hosted.json").write_text(
        json.dumps([{"offset": o, "response": d} for o, d in parts], ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# Google Gemini: the Interactions API, with the Files API for large parts
# ---------------------------------------------------------------------------------------------

GEMINI_SCHEMA = {  # the JSON a general Gemini model is asked for (the transcribe model has its own)
    "type": "object",
    "properties": {"segments": {"type": "array", "items": {
        "type": "object",
        "properties": {"start": {"type": "number", "description": "Seconds from the start of the audio"},
                       "end": {"type": "number", "description": "Seconds from the start of the audio"},
                       "speaker": {"type": "string", "description": "A, B, C, ... told apart by voice"},
                       "text": {"type": "string", "description": "What was said, word for word"}},
        "required": ["start", "end", "speaker", "text"]}}},
    "required": ["segments"]}


def gemini(cfg, model, job_dir, job, on_progress, cancelled):
    """Gemini API. gemini-3.5-transcribe takes a transcription_config and returns every word with
    its times and speaker; a general Gemini model gets instructions and a JSON schema instead.
    With word times one request takes at most 30 minutes of audio, so longer recordings go in
    parts cut at pauses. A part too large to send inline goes through the Files API and is
    deleted there afterwards. store=false asks Google not to keep the request."""
    opts = job.get("options") or {}
    base, headers = model["base_url"].rstrip("/"), {"x-goog-api-key": cfg.api_key(model)}
    parts_dir = Path(job_dir) / "parts"
    try:
        pieces = split_at_pauses(Path(job_dir) / "audio.flac", model.get("max_minutes", 29) * 60, parts_dir, cancelled)
        parts = []
        for i, (offset, piece) in enumerate(pieces):
            waiting = waiting_state(model, i, len(pieces))
            parts.append((offset, gemini_part(base, headers, model, opts, piece, waiting, on_progress, cancelled)))
    finally:
        shutil.rmtree(parts_dir, ignore_errors=True)
    save_raw(job_dir, parts)
    return {"lines": parts_lines(parts, gemini_tokens, opts.get("speakers", "none") != "none"), "meta": {}}


def gemini_part(base, headers, model, opts, piece, waiting, on_progress, cancelled):
    """Transcribe one part: inline when the request stays under the size limit, else uploaded."""
    size, uploaded = piece.stat().st_size, None
    try:
        if (size + 2) // 3 * 4 + 65536 <= model.get("inline_limit_mb", 20) * 1e6:  # base64 plus the rest
            audio = {"data": base64.b64encode(piece.read_bytes()).decode()}
        else:
            uploaded = gemini_upload(base, headers, model, piece, on_progress, cancelled)
            audio = {"uri": uploaded["uri"]}
        body = gemini_request(model, opts, audio)
        on_progress(waiting)
        r = wait_for(lambda: requests.post(f"{base}/v1beta/interactions", json=body, headers=headers,
                                           timeout=(30, 1800)), cancelled, tick=lambda: on_progress(waiting))
        check(r, "Gemini")
        data = r.json()
    finally:
        if uploaded and model.get("delete_after", True):
            gemini_delete(base, headers, uploaded["name"])
    if data.get("status") in ("failed", "cancelled", "incomplete"):
        raise EngineError(f"Gemini: the transcription came back {data['status']}")
    return data


def gemini_request(model, opts, audio):
    """The interactions.create body for one part; audio is {"data": base64} or {"uri": file uri}."""
    name = model.get("api_model", "gemini-3.5-transcribe")
    audio = {"type": "audio", "mime_type": "audio/flac", **audio}
    if "transcribe" not in name:
        return {"model": name, "store": False, "input": [{"type": "text", "text": gemini_prompt(opts)}, audio],
                "response_format": {"type": "text", "mime_type": "application/json", "schema": GEMINI_SCHEMA}}
    mode = {"type": "verbatim", "timestamp_granularities": ["word"]}  # no vocabulary with word times
    if opts.get("speakers", "none") != "none":  # up to 8 speakers; the number can't be given
        mode["diarization_mode"] = "speaker"
    codes = {"en": ["en-US"], "auto": []}.get(opts.get("language"), model.get("language_codes", []))
    return {"model": name, "store": False, "input": [audio],
            "generation_config": {"transcription_config": {"language_codes": codes, "mode": mode}}}


def gemini_prompt(opts):
    """Instructions for a general Gemini model (the transcribe model takes none)."""
    language = {"en": "People speak English.",
                "auto": "Write every language in its own script, as it is spoken."}.get(
        opts.get("language"), "People speak Egyptian Arabic with English terms. Write the Arabic as it is "
        "spoken, in Egyptian Arabic, not Modern Standard Arabic, and keep English words in Latin script.")
    speakers = opts.get("speakers", "none")
    if speakers == "none":
        who = "Use the speaker label A throughout."
    else:
        who = "Label the speakers A, B, C and so on by their voices" + (
            "." if speakers == "auto" else f"; there are {speakers} of them.")
    terms = split_terms(opts.get("prompt"))
    vocabulary = f" Names and terms that may occur: {', '.join(terms)}." if terms else ""
    return (f"Transcribe this recording of a meeting word for word. {language} Split it into segments of "
            f"one speaker each, with start and end times in seconds from the start of this audio. {who}{vocabulary}")


def gemini_upload(base, headers, model, piece, on_progress, cancelled):
    """Files API resumable upload: a start request with the metadata returns an upload address,
    then the bytes go there in one request. Returns the file resource once it is ACTIVE."""
    size, service = piece.stat().st_size, model["service"]
    start = {**headers, "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
             "X-Goog-Upload-Header-Content-Length": str(size), "X-Goog-Upload-Header-Content-Type": "audio/flac"}
    r = requests.post(f"{base}/upload/v1beta/files", json={"file": {"display_name": piece.name}}, headers=start, timeout=60)
    check(r, "Gemini")
    url = r.headers.get("X-Goog-Upload-URL")
    if not url:
        raise EngineError("Gemini: the Files API returned no upload address")
    body = FileBody(piece, upload_progress(on_progress, service), cancelled)
    try:
        on_progress({"stage": "uploading", "done": 0, "total": size, "service": service})
        r = requests.post(url, data=body, headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
                          timeout=(30, 3600))
    finally:
        body.close()
    check(r, "Gemini")
    file = r.json().get("file") or {}
    if not re.fullmatch(r"files/[\w-]+", str(file.get("name", ""))) or not file.get("uri"):
        raise EngineError("Gemini: the Files API returned no file")
    try:  # audio is usually ACTIVE at once; wait while it is PROCESSING
        for _ in range(300):
            if file.get("state") in (None, "ACTIVE", "STATE_UNSPECIFIED"):
                return file
            if file.get("state") != "PROCESSING":
                raise EngineError(f"Gemini: the uploaded file is {file.get('state')}")
            pause(model.get("poll_s", 2), cancelled)
            r = requests.get(f"{base}/v1beta/{file['name']}", headers=headers, timeout=60)
            check(r, "Gemini")
            file = {**file, **r.json()}
        raise EngineError("Gemini: the uploaded file is still being processed")
    except BaseException:
        gemini_delete(base, headers, file["name"])
        raise


def gemini_delete(base, headers, name):
    try:  # Google would delete it after 48 hours anyway
        requests.delete(f"{base}/v1beta/{name}", headers=headers, timeout=30)
    except requests.RequestException:
        pass


def gemini_tokens(data):
    """Words from one response: the word_info annotations of the transcribe model, or the
    segments of a general model's JSON, or else the plain text."""
    contents = [c for s in data.get("steps") or [] if s.get("type") == "model_output"
                for c in s.get("content") or [] if c.get("type") == "text"]
    tokens, t = [], 0.0
    for w in (a for c in contents for a in c.get("annotations") or [] if a.get("type") == "word_info"):
        text = (w.get("text") or "").strip()
        if text:
            start = seconds(w.get("start_offset"), t)
            t = seconds(w.get("end_offset"), start)
            tokens.append(token(start, t, text, w.get("speaker")))
    if tokens:
        return tokens
    text = "".join(c.get("text") or "" for c in contents).strip()
    try:
        segments = json.loads(text).get("segments")
    except (ValueError, AttributeError):
        segments = None
    if isinstance(segments, list):
        return [token(seconds(s.get("start")), seconds(s.get("end")), str(s.get("text")).strip(), s.get("speaker") or None)
                for s in segments if isinstance(s, dict) and str(s.get("text") or "").strip()]
    return [token(0.0, 0.0, text, None)] if text else []


# ---------------------------------------------------------------------------------------------
# Deepgram: pre-recorded /v1/listen with the audio as the body
# ---------------------------------------------------------------------------------------------

def deepgram(cfg, model, job_dir, job, on_progress, cancelled):
    """Deepgram pre-recorded API: one request with the audio as the body returns the transcript.
    mip_opt_out=true keeps the audio out of Deepgram's model training; Deepgram stores no
    transcripts, so there is nothing to delete."""
    opts = job.get("options") or {}
    base, service = model["base_url"].rstrip("/"), model["service"]
    params = [("model", model.get("api_model", "nova-3")),
              ("language", "en" if opts.get("language") == "en" else model.get("language", "ar-EG")),  # can't detect Arabic
              ("smart_format", "true"), ("utterances", "true")]
    if model.get("mip_opt_out", True):
        params.append(("mip_opt_out", "true"))
    if opts.get("speakers", "none") != "none":  # the number of speakers can't be given
        params.append(("diarize_model", model.get("diarize_model", "latest")))
    if model.get("prompt"):  # key terms: repeated parameters, at most 100 (500 tokens in all)
        params += [("keyterm", t) for t in split_terms(opts.get("prompt"))[:100]]
    waiting = waiting_state(model, 0, 1)
    body = FileBody(Path(job_dir) / "audio.flac", upload_then_wait(on_progress, service, waiting), cancelled)

    def send():
        try:
            return requests.post(f"{base}/v1/listen", params=params, data=body, timeout=(30, 1800), headers={
                "Authorization": f"Token {cfg.api_key(model)}", "Content-Type": "audio/flac"})
        finally:
            body.close()
    on_progress({"stage": "uploading", "done": 0, "total": len(body), "service": service})
    r = wait_for(send, cancelled, tick=lambda: body.sent >= len(body) and on_progress(waiting))
    check(r, "Deepgram")
    data = r.json()
    (Path(job_dir) / "hosted.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"lines": deepgram_lines(data), "meta": {}}


def deepgram_lines(data):
    """The words of the utterances (speaker per word with diarization), else the channel's words."""
    results = data.get("results") or {}
    words = [{**w, "speaker": w.get("speaker", u.get("speaker"))}
             for u in results.get("utterances") or [] for w in u.get("words") or []]
    alternative = ((results.get("channels") or [{}])[0].get("alternatives") or [{}])[0]
    words = words or alternative.get("words") or []
    names = relabel(w.get("speaker") for w in words)
    tokens = [token(float(w.get("start") or 0), float(w.get("end") or 0), text, names.get(w.get("speaker")))
              for w in words if (text := (w.get("punctuated_word") or w.get("word") or "").strip())]
    if not tokens and (alternative.get("transcript") or "").strip():
        return [{"start": 0.0, "end": 0.0, "speaker": None, "text": alternative["transcript"].strip()}]
    return lines_from_words(tokens)


# ---------------------------------------------------------------------------------------------
# AssemblyAI: upload, create a transcript, poll, fetch, delete
# ---------------------------------------------------------------------------------------------

def assemblyai(cfg, model, job_dir, job, on_progress, cancelled):
    """AssemblyAI: upload the audio, create a transcript, poll until it is done, then delete it
    there, which also deletes the uploaded audio."""
    opts = job.get("options") or {}
    base, auth, service = model["base_url"].rstrip("/"), {"authorization": cfg.api_key(model)}, model["service"]
    body = FileBody(Path(job_dir) / "audio.flac", upload_progress(on_progress, service), cancelled)
    try:
        on_progress({"stage": "uploading", "done": 0, "total": len(body), "service": service})
        r = requests.post(f"{base}/v2/upload", data=body, timeout=(30, 3 * 3600),
                          headers={**auth, "Content-Type": "application/octet-stream"})
    finally:
        body.close()
    check(r, "AssemblyAI")
    r = requests.post(f"{base}/v2/transcript", json=assemblyai_request(model, opts, r.json()["upload_url"]),
                      headers=auth, timeout=60)
    check(r, "AssemblyAI")
    tid = str(r.json()["id"])
    if not re.fullmatch(r"[\w-]+", tid):
        raise EngineError("AssemblyAI: unexpected transcript id")
    (Path(job_dir) / "hosted-job.txt").write_text(tid, encoding="utf-8")
    try:
        data = assemblyai_wait(base, auth, tid, model, on_progress, cancelled)
        (Path(job_dir) / "hosted.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except BaseException:
        assemblyai_delete(base, auth, tid, model.get("delete_retry_s", 30))
        raise
    if model.get("delete_after", True):
        assemblyai_delete(base, auth, tid, model.get("delete_retry_s", 30))
    return {"lines": assemblyai_lines(data), "meta": {"detected_language": data.get("language_code")}}


def assemblyai_request(model, opts, audio_url):
    req = {"audio_url": audio_url, "speech_models": model.get("speech_models", ["universal-3-5-pro", "universal-2"])}
    if opts.get("language") == "en":
        req["language_code"] = "en"
    else:  # Universal-3.5 Pro follows switches between languages when it detects them
        req["language_detection"] = True
        if opts.get("language") != "auto" and model.get("expected_languages"):
            req["language_detection_options"] = {"expected_languages": model["expected_languages"]}
    speakers = opts.get("speakers", "none")
    if speakers != "none":
        req["speaker_labels"] = True
        if speakers != "auto":  # the exact number
            req["speakers_expected"] = int(speakers)
    if model.get("prompt"):  # key terms: at most 6 words each; 200 fit Universal-2 too
        terms = [t for t in split_terms(opts.get("prompt")) if len(t.split()) <= 6]
        if terms:
            req["keyterms_prompt"] = terms[:200]
    return req


def assemblyai_wait(base, auth, tid, model, on_progress, cancelled):
    t0 = time.time()
    while True:
        r = requests.get(f"{base}/v2/transcript/{tid}", headers=auth, timeout=60)
        if r.status_code != 429:
            check(r, "AssemblyAI")
            data = r.json()
            if data.get("status") == "completed":
                return data
            if data.get("status") == "error":
                raise EngineError(f"AssemblyAI: {data.get('error') or 'the transcription failed'}")
        on_progress({"stage": "remote", "service": model["service"], "elapsed": round(time.time() - t0)})
        pause(model.get("poll_s", 3), cancelled)


def assemblyai_delete(base, auth, tid, retry_s=30, tries=40):
    """Delete a transcript there, with its uploaded audio. One that is still being processed (after
    a cancel) may not be deletable yet, so a background thread tries again every retry_s seconds."""
    def attempt():
        try:
            return requests.delete(f"{base}/v2/transcript/{tid}", headers=auth, timeout=30).status_code in (200, 204, 404)
        except requests.RequestException:
            return False

    def retry():
        for _ in range(tries):
            time.sleep(retry_s)
            if attempt():
                return
    if not attempt():
        threading.Thread(target=retry, daemon=True, name="sedjem-delete").start()


def assemblyai_lines(data):
    """The words of the utterances when speaker labels are on, else all words; times in ms."""
    words = [{**w, "speaker": w.get("speaker") or u.get("speaker")}  # an utterance without words is one
             for u in data.get("utterances") or [] for w in u.get("words") or [u]]
    words = words or data.get("words") or []
    names = relabel(w.get("speaker") for w in words)
    tokens = [token(float(w.get("start") or 0) / 1000, float(w.get("end") or 0) / 1000, text, names.get(w.get("speaker")))
              for w in words if (text := (w.get("text") or "").strip())]
    if not tokens and (data.get("text") or "").strip():
        return [{"start": 0.0, "end": 0.0, "speaker": None, "text": data["text"].strip()}]
    return lines_from_words(tokens)


# ---------------------------------------------------------------------------------------------
# Azure AI Speech: fast transcription (synchronous, multipart)
# ---------------------------------------------------------------------------------------------

def azure(cfg, model, job_dir, job, on_progress, cancelled):
    """Azure AI Speech fast transcription: one multipart request with the audio and a JSON
    definition returns phrases with times and speakers. Microsoft stores neither, so there is
    nothing to delete. The REST reference allows under 2 hours per request (the how-to guide
    says 5), so longer recordings go in parts cut at pauses."""
    opts, service = job.get("options") or {}, model["service"]
    url = f"{azure_endpoint(cfg, model)}/speechtotext/transcriptions:transcribe"
    params, headers = {"api-version": model.get("api_version", "2025-10-15")}, {"Ocp-Apim-Subscription-Key": cfg.api_key(model)}
    definition = json.dumps(azure_definition(model, opts), ensure_ascii=False)
    parts_dir = Path(job_dir) / "parts"
    try:
        pieces = split_at_pauses(Path(job_dir) / "audio.flac", model.get("max_minutes", 115) * 60, parts_dir, cancelled)
        parts = []
        for i, (offset, piece) in enumerate(pieces):
            waiting = waiting_state(model, i, len(pieces))
            body = Multipart([("definition", definition)], "audio", piece, "audio.flac", "audio/flac",
                             upload_then_wait(on_progress, service, waiting), cancelled)

            def send(body=body):
                try:
                    return requests.post(url, params=params, data=body, timeout=(30, 3600),
                                         headers={**headers, "Content-Type": body.content_type})
                finally:
                    body.close()
            on_progress({"stage": "uploading", "done": 0, "total": len(body), "service": service})
            r = wait_for(send, cancelled, tick=lambda body=body, waiting=waiting: body.sent >= len(body) and on_progress(waiting))
            check(r, "Azure Speech")
            parts.append((offset, r.json()))
    finally:
        shutil.rmtree(parts_dir, ignore_errors=True)
    save_raw(job_dir, parts)
    return {"lines": parts_lines(parts, azure_tokens), "meta": {}}


def azure_endpoint(cfg, model):
    """https://<region>.api.cognitive.microsoft.com for the region saved in Settings (a key works
    only in its resource's region), or the endpoint in the config, e.g. a custom domain."""
    if model.get("endpoint"):
        return model["endpoint"].rstrip("/")
    region = cfg.region(model)
    if not re.fullmatch(r"[a-z0-9]{2,40}", region):
        raise EngineError("Azure Speech: set the region of your Speech resource in Settings, for example westeurope")
    return f"https://{region}.api.cognitive.microsoft.com"


def azure_definition(model, opts):
    """One locale is used as given; several make it pick one language for the whole file."""
    locales = {"en": ["en-US"], "auto": model.get("auto_locales", ["ar-EG", "en-US"])}.get(
        opts.get("language"), [model.get("locale", "ar-EG")])
    definition = {"locales": locales, "profanityFilterMode": model.get("profanity_filter", "None")}
    speakers = opts.get("speakers", "none")
    if speakers != "none":  # maxSpeakers is 2 to 35
        n = model.get("max_speakers", 10) if speakers == "auto" else int(speakers)
        definition["diarization"] = {"enabled": True, "maxSpeakers": min(35, max(2, n))}
    if model.get("prompt"):
        phrases = split_terms(opts.get("prompt"))[:2000]
        if phrases:
            definition["phraseList"] = {"phrases": phrases}
    return definition


def azure_tokens(data):
    """Phrases, times in milliseconds; speaker is there when diarization is on."""
    tokens = []
    for p in data.get("phrases") or []:
        text = (p.get("text") or "").strip()
        if text:
            start = float(p.get("offsetMilliseconds") or 0) / 1000
            tokens.append(token(start, start + float(p.get("durationMilliseconds") or 0) / 1000, text, p.get("speaker")))
    if not tokens:
        text = " ".join(c.get("text") or "" for c in data.get("combinedPhrases") or []).strip()
        return [token(0.0, 0.0, text, None)] if text else []
    return tokens


engines.HOSTED.update({"gemini": gemini, "deepgram": deepgram, "assemblyai": assemblyai, "azure": azure})
