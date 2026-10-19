"""Transcription jobs: one folder each under <storage>/jobs, and the background runner.

A job folder holds job.json (settings and status), audio.flac (16 kHz mono, used for every model
and for playback), transcript.json once done, and the engine's own output and log. Jobs go
through prepare (convert the recording) → queued → running → done / failed / cancelled. Local and
hosted jobs have separate queues, so an upload doesn't wait for a long local run.

A job made from a YouTube link (source_url, app/youtube.py) is downloaded first, one at a time in the
order the jobs were added (a playlist's in playlist order): video.mp4, or only the sound as source.<ext>,
and captions.vtt when the video has captions made by people. Prepare then reads video.mp4 like any
recording, and a job with captions takes them as its transcript instead of going to a model. A finished
YouTube job writes its files to the courses folder (app/courses.py).
"""
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import soundfile as sf

import sysinfo

from . import courses, engines, report, youtube
from .config import power_profile, replace_file, set_power_profile

ACTIVE = ("preparing", "queued", "running")
ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path, tries=40):
    """A JSON file written by write_json, or None if it doesn't exist. On Windows a read fails while
    another thread is replacing the file, so a denied read is retried for up to about a second."""
    for i in range(tries):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            if i >= 2:  # really missing (a replace can hide it for an instant)
                return None
        except (PermissionError, ValueError):
            pass
        time.sleep(0.025)
    return None


def write_json(path, data):
    tmp = Path(path).with_name(f"{Path(path).name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    replace_file(tmp, path)


class Store:
    def __init__(self, storage):
        self.root = Path(storage) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def new_id(self):
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]

    def dir(self, jid):
        if not ID_RE.match(jid or ""):
            raise KeyError(jid)
        return self.root / jid

    def get(self, jid):
        try:
            return read_json(self.dir(jid) / "job.json")
        except KeyError:
            return None

    def create(self, meta):
        with self.lock:
            (self.root / meta["id"]).mkdir(parents=True)
            write_json(self.root / meta["id"] / "job.json", meta)
        return meta

    def update(self, jid, **fields):
        with self.lock:
            meta = self.get(jid)
            if meta is None:
                return None
            meta.update(fields)
            write_json(self.dir(jid) / "job.json", meta)
            return meta

    def list(self):
        jobs = [self.get(p.name) for p in self.root.iterdir() if (p / "job.json").exists()]
        # newest first; the id (which starts with the time) settles ties, so the order doesn't depend on the folder listing
        return sorted((j for j in jobs if j), key=lambda j: (j["created"], j["id"]), reverse=True)

    def delete(self, jid):
        with self.lock:
            folder = self.dir(jid)
            for _ in range(20):  # Windows: a file still open (e.g. by the audio player) blocks deletion briefly
                shutil.rmtree(folder, ignore_errors=True)
                if not folder.exists():
                    return
                time.sleep(0.25)

    def transcript(self, jid):
        try:
            return read_json(self.dir(jid) / "transcript.json")
        except KeyError:
            return None

    def save_transcript(self, jid, data):
        write_json(self.dir(jid) / "transcript.json", data)


def probe_seconds(path):
    """Duration of any audio or video file, in seconds; None if unknown."""
    import av
    try:
        with av.open(str(path)) as c:
            if c.duration:
                return c.duration / av.time_base
            s = next((s for s in c.streams if s.type == "audio"), None)
            return float(s.duration * s.time_base) if s is not None and s.duration and s.time_base else None
    except Exception:  # not a media file, or unreadable: the conversion reports the details
        return None


def convert(source, dest, on_progress, cancelled):
    """Decode any audio or video file to 16 kHz mono FLAC with PyAV (the FFmpeg libraries that
    faster-whisper already ships), so no separate ffmpeg install is needed on any platform."""
    import av
    tmp = dest.with_name(dest.stem + ".tmp.flac")
    try:
        with av.open(str(source)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise engines.EngineError("the file has no audio track")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            last = 0.0
            with sf.SoundFile(str(tmp), "w", samplerate=16000, channels=1, subtype="PCM_16", format="FLAC") as out:
                for packet in container.demux(stream):  # the last packet flushes the decoder
                    if cancelled():
                        raise engines.Cancelled()
                    try:
                        frames = packet.decode()
                    except av.error.InvalidDataError:  # a damaged packet: skip it
                        continue
                    for frame in frames:
                        for f in resampler.resample(frame):
                            out.write(f.to_ndarray().reshape(-1))
                        if frame.time is not None and time.time() - last > 0.5:
                            last = time.time()
                            on_progress(frame.time)
                for f in resampler.resample(None):
                    out.write(f.to_ndarray().reshape(-1))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    replace_file(tmp, dest)


class Runner:
    def __init__(self, store, cfg, gpus=None):
        self.store, self.cfg = store, cfg
        self.gpus = gpus or (lambda: None)  # the graphics cards the app found, for the job details
        self.queues = {"download": queue.Queue(), "prepare": queue.Queue(), "local": queue.Queue(), "hosted": queue.Queue()}
        self.cancelled = set()
        self.procs = {}  # job id -> running transcribe.py process
        self.saved_profile = None
        for name, fn in (("download", self.download), ("prepare", self.prepare), ("local", self.work), ("hosted", self.work)):
            threading.Thread(target=self.loop, args=(name, fn), daemon=True, name=f"sedjem-{name}").start()

    # ---- queueing -----------------------------------------------------------------------------
    def submit(self, jid):
        job, folder = self.store.get(jid), self.store.dir(jid)
        if job and job.get("source_url") and not any((folder / n).exists() for n in ("audio.flac", "video.mp4")) \
                and next(folder.glob("source.*"), None) is None:  # a YouTube job not downloaded yet
            self.store.update(jid, status="preparing", stage="waiting-download", error=None)
            self.queues["download"].put(jid)
            return
        self.store.update(jid, status="preparing", stage="waiting")
        self.queues["prepare"].put(jid)

    def recover(self):
        """After a restart: requeue jobs that were waiting (a playlist's in playlist order); mark the ones that
        were running."""
        order = lambda j: (j["created"], (j.get("course") or {}).get("index") or 0, j["id"])
        for job in sorted(self.store.list(), key=order):
            if job["status"] in ("preparing", "queued"):
                self.submit(job["id"])
            elif job["status"] == "running":
                self.store.update(job["id"], status="interrupted", stage=None,
                                  error="The app was closed while this was running.")

    def cancel(self, jid):
        self.cancelled.add(jid)
        job = self.store.get(jid)
        if job and job["status"] in ("preparing", "queued"):
            self.store.update(jid, status="cancelled", stage=None, finished=now())

    def shutdown(self):
        """Stop running local models (they run in their own process group)."""
        for jid, proc in list(self.procs.items()):
            self.cancelled.add(jid)
            engines.stop(proc)

    def loop(self, name, fn):
        while True:
            jid = self.queues[name].get()
            try:
                fn(jid, name)
            except Exception as e:  # never let one job kill the worker
                self.store.update(jid, status="failed", stage=None, error=f"{type(e).__name__}: {e}", finished=now())

    # ---- steps ----------------------------------------------------------------------------------
    def download(self, jid, _):
        """Download a YouTube job's video (app/youtube.py), then hand it to prepare."""
        job = self.store.get(jid)
        if not job or job["status"] == "cancelled" or jid in self.cancelled:
            return
        self.store.update(jid, status="preparing", stage="downloading", done=0, total=None, error=None)
        last = [0.0]

        def progress(done, total):
            if time.time() - last[0] > 0.5:
                last[0] = time.time()
                self.store.update(jid, done=done, total=total)

        try:
            got = youtube.download(self.cfg, job, self.store.dir(jid), progress, lambda: jid in self.cancelled)
        except engines.Cancelled:
            return
        except youtube.YouTubeError as e:
            self.store.update(jid, status="failed", stage=None, done=None, total=None, error=f"YouTube: {e}",
                              finished=now())
            return
        if jid in self.cancelled or self.store.get(jid) is None:  # cancelled or deleted meanwhile
            return
        self.store.update(jid, stage="waiting", done=None, total=None, **got)
        self.queues["prepare"].put(jid)

    def prepare(self, jid, _):
        """Convert the recording to 16 kHz mono FLAC (used by every model and for playback)."""
        job = self.store.get(jid)
        if not job or job["status"] == "cancelled" or jid in self.cancelled:
            return
        folder = self.store.dir(jid)
        audio = folder / "audio.flac"
        if not audio.exists():
            source = Path(job["source_path"]) if job.get("source_path") else next(folder.glob("source.*"), None) \
                or (folder / "video.mp4" if (folder / "video.mp4").exists() else None)  # e.g. a YouTube video
            if not source or not source.exists():
                raise engines.EngineError("the recording is missing")
            total = probe_seconds(source)
            self.store.update(jid, status="preparing", stage="converting", done=0, total=total,
                              source=sysinfo.recording(source, job.get("source_name")))  # before an upload is deleted
            try:
                convert(source, audio, lambda t: self.store.update(jid, done=t), lambda: jid in self.cancelled)
            except engines.Cancelled:
                return
            except engines.EngineError:
                raise
            except Exception as e:
                raise engines.EngineError(f"could not read the recording: {e}")
            if not job.get("source_path") and source.name.startswith("source.") and not self.cfg.keep_original:
                source.unlink(missing_ok=True)  # an upload (video.mp4 stays: it is played beside the transcript)
        info = sf.info(str(audio))
        model = self.cfg.models.get(job["model"])
        if model is None and not job.get("captions"):
            raise engines.EngineError(f"unknown model {job['model']}")
        from .compare import try_fingerprint  # the same recording added again joins this one's group
        ready = {"done": None, "total": None, "audio_s": round(info.duration, 2), "queued": now(),
                 "fingerprint": job.get("fingerprint") or try_fingerprint(audio)}
        if job.get("captions"):  # captions made by people: they are the transcript, and no model runs
            return self.finish_captions(jid, ready)
        self.store.update(jid, status="queued", stage=None, **ready)
        self.queues[model["kind"]].put(jid)

    def finish_captions(self, jid, fields):
        """A YouTube job with captions made by people: its transcript is the captions, with one speaker."""
        try:
            lines = youtube.captions_transcript(self.store.dir(jid))
        except (OSError, ValueError) as e:
            raise engines.EngineError(f"could not read the captions: {e}")
        self.store.save_transcript(jid, {"lines": lines, "edited": False, "model": youtube.CAPTIONS_MODEL})
        self.store.update(jid, status="done", stage=None, started=now(), finished=now(), error=None,
                          model=youtube.CAPTIONS_MODEL, model_title=youtube.CAPTIONS_TITLE, kind="captions",
                          engine=None, app_version=report.__version__, **fields)
        from . import history  # imported here because it uses this module's helpers
        history.current(self.store, jid, self.cfg)  # version 0: the captions as YouTube has them
        courses.try_refresh(self.store, self.cfg, self.store.get(jid))

    def work(self, jid, kind):
        job = self.store.get(jid)
        if not job or job["status"] != "queued" or jid in self.cancelled:
            return
        model = self.cfg.models[job["model"]]
        folder = self.store.dir(jid)
        self.store.update(jid, status="running", stage="starting", started=now(), done=None, total=None,
                          error=None, elapsed=None, **report.run_facts(self.cfg, model, self.gpus()))
        t0 = time.time()
        last_write = [0.0]

        def on_progress(state):
            if time.time() - last_write[0] > 0.4 or state.get("stage") != "transcribing":
                last_write[0] = time.time()
                self.store.update(jid, stage=state.get("stage"), done=state.get("done"), total=state.get("total"),
                                  elapsed=round(time.time() - t0, 1))

        def cancelled():
            return jid in self.cancelled

        try:
            if kind == "local":
                self.performance(True)
                self.store.update(jid, power=power_profile())  # the estimates compare like with like
                result = engines.run_local(self.cfg, model, folder, job, on_progress, cancelled,
                                           register=lambda p: self.procs.__setitem__(jid, p))
            else:
                result = engines.run_hosted(self.cfg, model, folder, job, on_progress, cancelled)
            took = time.time() - t0
            self.store.save_transcript(jid, {"lines": result["lines"], "edited": False, "model": job["model"]})
            self.store.update(jid, status="done", stage=None, done=None, total=None, finished=now(),
                              seconds=round(took, 1), rtf=round(took / max(1e-6, job.get("audio_s") or 1), 3),
                              **result.get("meta", {}))
            from . import history  # imported here because it uses this module's helpers
            history.current(self.store, jid, self.cfg)  # starts the history: version 0 is the model's output
            courses.try_refresh(self.store, self.cfg, self.store.get(jid))  # a YouTube job's files
        except engines.Cancelled:
            self.store.update(jid, status="cancelled", stage=None, finished=now())
        except Exception as e:
            self.store.update(jid, status="failed", stage=None, error=str(e) or type(e).__name__, finished=now())
        finally:
            self.procs.pop(jid, None)
            self.cancelled.discard(jid)
            if kind == "local" and self.queues["local"].empty():
                self.performance(False)

    def performance(self, on):
        """With performance_while_running: switch to the performance profile while local jobs run. Switching
        back doesn't depend on the setting, which may have been turned off in the meantime."""
        if on and self.saved_profile is None and self.cfg.setting("performance_while_running"):
            current = power_profile()
            if current and current != "performance" and set_power_profile("performance"):
                self.saved_profile = current
        elif not on and self.saved_profile:
            set_power_profile(self.saved_profile)
            self.saved_profile = None

    # ---- new jobs ---------------------------------------------------------------------------------
    def rerun(self, jid, model_id, options):
        """A new job on the same audio with another model or other options."""
        old = self.store.get(jid)
        youtube_fields = {k: old[k] for k in ("source_url", "youtube_id", "course") if old.get(k)}  # still the lecture
        new = self.new_job(title=old["title"], source_name=old.get("source_name"), model_id=model_id,
                           options=options, rerun_of=jid, source=old.get("source"),  # the same recording
                           fingerprint=old.get("fingerprint"), **youtube_fields)
        src, dst = self.store.dir(jid) / "audio.flac", self.store.dir(new["id"]) / "audio.flac"
        try:
            os.link(src, dst)  # same file on disk, no extra space
        except OSError:
            shutil.copy2(src, dst)
        self.submit(new["id"])
        return self.store.get(new["id"])

    def new_job(self, title, source_name, model_id, options, **extra):
        model = self.cfg.models[model_id]
        for _ in range(50):  # a playlist adds many jobs in the same second: an id that is taken is drawn again
            meta = {"id": self.store.new_id(), "title": title, "source_name": source_name, "created": now(),
                    "model": model_id, "model_title": model["title"], "kind": model["kind"], "options": options,
                    "status": "preparing", "stage": "waiting", "speaker_names": {}, **extra}
            try:
                return self.store.create(meta)
            except FileExistsError:
                continue
        raise RuntimeError("no free id for a new job")
