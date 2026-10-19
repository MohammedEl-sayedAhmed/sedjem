"""Transcription jobs: one folder each under <storage>/jobs, and the background runner.

A job folder holds job.json (settings and status), audio.flac (16 kHz mono, used for every model
and for playback), transcript.json once done, and the engine's own output and log. A recording with
a picture also gets video.mp4 for the job page (app/video.py). Jobs go through prepare (convert the
recording) → queued → running → done / failed / cancelled. Local and hosted jobs have separate queues,
so an upload doesn't wait for a long local run; the video is made on its own thread meanwhile.
"""
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import soundfile as sf

import sysinfo

from . import engines, report, video
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


def link_or_copy(src, dst):
    try:
        os.link(src, dst)  # same file on disk, no extra space
    except OSError:
        shutil.copy2(src, dst)


def note(folder, text):
    """A line in the job's log (log.txt)."""
    try:
        with open(Path(folder) / "log.txt", "a", encoding="utf-8") as f:
            f.write(f"{now()} {text}\n")
    except OSError:  # the job was deleted meanwhile
        pass


def lower_priority():
    """Let the transcription have the processor first: the calling thread, and the encoder's threads it
    starts, run at a lower priority (Linux, where a thread's niceness is its own; elsewhere unchanged)."""
    if sys.platform.startswith("linux"):
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except (AttributeError, OSError):
            pass


class Runner:
    def __init__(self, store, cfg, gpus=None):
        self.store, self.cfg = store, cfg
        self.gpus = gpus or (lambda: None)  # the graphics cards the app found, for the job details
        self.queues = {"prepare": queue.Queue(), "video": queue.Queue(), "local": queue.Queue(), "hosted": queue.Queue()}
        self.cancelled = set()
        self.procs = {}  # job id -> running transcribe.py process
        self.videos = set()  # jobs whose video.mp4 is waiting or being made
        self.making = None  # the one being made
        self.unwanted = set()  # jobs whose video should stop (the job is being deleted)
        self.saved_profile = None
        for name, fn in (("prepare", self.prepare), ("video", self.make_video), ("local", self.work), ("hosted", self.work)):
            threading.Thread(target=self.loop, args=(name, fn), daemon=True, name=f"sedjem-{name}").start()

    # ---- queueing -----------------------------------------------------------------------------
    def submit(self, jid):
        self.store.update(jid, status="preparing", stage="waiting")
        self.queues["prepare"].put(jid)

    def recover(self):
        """After a restart: requeue jobs that were waiting; mark the ones that were running."""
        for job in reversed(self.store.list()):
            if job["status"] in ("preparing", "queued"):
                self.submit(job["id"])  # and prepare queues its video again if it wasn't finished
                continue
            if job["status"] == "running":
                self.store.update(job["id"], status="interrupted", stage=None,
                                  error="The app was closed while this was running.")
            if job.get("video_prep"):
                self.queue_video(job["id"])

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
                if name != "video":  # (the video is extra: make_video says why it has none)
                    self.store.update(jid, status="failed", stage=None, error=f"{type(e).__name__}: {e}", finished=now())

    # ---- steps ----------------------------------------------------------------------------------
    def source(self, job, folder):
        """The recording a job is made from: a file used in place, an upload (source.*), or a video.mp4
        put in the folder before (a download); None once an upload has been deleted."""
        if job.get("source_path"):
            found = Path(job["source_path"])
        else:
            found = next(folder.glob("source.*"), None) or folder / video.NAME
        return found if found.is_file() else None

    def drop_upload(self, job, source):
        """Delete an uploaded recording once audio.flac (and the video) are made from it, unless kept."""
        if source and not job.get("source_path") and source.name != video.NAME and not self.cfg.keep_original:
            source.unlink(missing_ok=True)

    def prepare(self, jid, _):
        """Convert the recording to 16 kHz mono FLAC (used by every model and for playback), and queue the
        making of video.mp4 when it has a picture."""
        job = self.store.get(jid)
        if not job or job["status"] == "cancelled" or jid in self.cancelled:
            return
        folder = self.store.dir(jid)
        audio, movie = folder / "audio.flac", folder / video.NAME
        source = self.source(job, folder)
        if not audio.exists():
            if not source:
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
        if movie.exists():  # made before, or downloaded with the recording
            if not job.get("video"):
                self.store.update(jid, video=video.size(movie))
        elif source and not job.get("video_skipped") and video.has_picture(source):
            self.queue_video(jid)  # made next to the transcription; the upload is deleted after it
        if jid not in self.videos:
            self.drop_upload(job, source)
        info = sf.info(str(audio))
        model = self.cfg.models.get(job["model"])
        if model is None:
            raise engines.EngineError(f"unknown model {job['model']}")
        from .compare import try_fingerprint  # the same recording added again joins this one's group
        self.store.update(jid, status="queued", stage=None, done=None, total=None, audio_s=round(info.duration, 2),
                          queued=now(), fingerprint=job.get("fingerprint") or try_fingerprint(audio))
        self.queues[model["kind"]].put(jid)

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

    # ---- the video on the job page (app/video.py) --------------------------------------------------
    def queue_video(self, jid):
        if jid not in self.videos:
            self.videos.add(jid)
            seconds = ((self.store.get(jid) or {}).get("source") or {}).get("duration")
            self.store.update(jid, video_prep={"done": None, "total": seconds})
            self.queues["video"].put(jid)

    def make_video(self, jid, _):
        """Make video.mp4 while the transcription goes on (at a lower priority, so it isn't slowed much).
        The job never fails over it: without a video, video_skipped says why."""
        lower_priority()
        source, self.making = None, jid
        try:
            job = self.store.get(jid)
            if not job or jid in self.unwanted:
                return
            folder = self.store.dir(jid)
            source = self.source(job, folder)
            total = (job.get("video_prep") or {}).get("total")
            result = {"video": None, "video_skipped": "the recording was no longer there to make the video from"}
            if source:
                try:
                    shown = video.make(source, folder / video.NAME,
                                       lambda t: self.store.update(jid, video_prep={"done": round(t, 1), "total": total}),
                                       lambda: jid in self.unwanted)
                    result = {"video": shown, "video_skipped": None if shown else "the recording has no picture"}
                except video.Skipped as e:
                    result["video_skipped"] = str(e)
                except engines.Cancelled:
                    return
                except Exception as e:  # noqa: BLE001 — a damaged or unusual file: the transcript is still made
                    result["video_skipped"] = f"the video could not be converted ({type(e).__name__}: {e})"
            if result["video_skipped"]:
                note(folder, f"No video on the job page: {result['video_skipped']}.")
            self.store.update(jid, video_prep=None, **result)
            if result["video"]:
                self.share_video(jid)
        finally:
            job = None if jid in self.unwanted else self.store.get(jid)
            if job:
                self.drop_upload(job, source)
            self.videos.discard(jid)
            self.unwanted.discard(jid)
            self.making = None

    def share_video(self, jid):
        """Give the new video to the jobs run again on this recording while it was being made."""
        for job in self.store.list():
            dst = self.store.dir(job["id"]) / video.NAME
            if job.get("rerun_of") == jid and not dst.exists() and not job.get("video"):
                link_or_copy(self.store.dir(jid) / video.NAME, dst)
                self.store.update(job["id"], video=self.store.get(jid).get("video"), video_prep=None)
                self.share_video(job["id"])

    def stop_video(self, jid, wait=15):
        """Stop making a job's video, before the job is deleted (a video still waiting is skipped)."""
        if jid in self.videos:
            self.unwanted.add(jid)
        end = time.time() + wait
        while self.making == jid and time.time() < end:
            time.sleep(0.05)

    # ---- new jobs ---------------------------------------------------------------------------------
    def rerun(self, jid, model_id, options):
        """A new job on the same audio (and video) with another model or other options."""
        old = self.store.get(jid)
        folder = self.store.dir(jid)
        shown = old.get("video") if (folder / video.NAME).exists() else None
        new = self.new_job(title=old["title"], source_name=old.get("source_name"), model_id=model_id,
                           options=options, rerun_of=jid, source=old.get("source"),  # the same recording
                           fingerprint=old.get("fingerprint"), **({"video": shown} if shown else {}))
        for name in ("audio.flac", video.NAME):
            if name == "audio.flac" or shown:
                link_or_copy(folder / name, self.store.dir(new["id"]) / name)
        self.submit(new["id"])
        return self.store.get(new["id"])

    def new_job(self, title, source_name, model_id, options, **extra):
        model = self.cfg.models[model_id]
        meta = {"id": self.store.new_id(), "title": title, "source_name": source_name, "created": now(),
                "model": model_id, "model_title": model["title"], "kind": model["kind"], "options": options,
                "status": "preparing", "stage": "waiting", "speaker_names": {}, **extra}
        return self.store.create(meta)
