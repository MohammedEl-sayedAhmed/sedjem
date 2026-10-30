"""Download local models from inside the app: resumable, verified, one item at a time.

Every file in the config has a pinned URL (a fixed Hugging Face revision or release), its size and
its SHA-256. A download goes to <file>.part, resumes with an HTTP Range request after a dropped
connection, and becomes the real file only once size and checksum match. A file with `unpack` is a
package (a wheel): the listed members are copied out next to it, checked by size, and the package is
deleted (a member marked executable in the package stays executable: Deno, app/youtube.py). Small files from Hugging Face have no SHA-256 in its listing, only their git blob id, so
those are checked by `git_sha1` instead. An item with `convert` (a Whisper checkpoint in Transformers
format, added from Hugging Face) is converted for faster-whisper once downloaded (app/hub.py), and
counts as installed when the converted model is there.
"""
import hashlib
import os
import shutil
import threading
import time
import zipfile

import requests

from .config import replace_file


class Cancelled(Exception):
    pass


class Downloads:
    def __init__(self, cfg, done=None):
        self.cfg = cfg
        self.on_done = done or (lambda item_id: None)  # a download finished: the usage counts (app/usage.py)
        self.lock = threading.Lock()
        self.jobs = {}        # item id -> {"state", "done", "total", "error", "file"}
        self.cancelled = set()

    # ---- state -----------------------------------------------------------------------------------
    def installed(self, f):
        if f.get("unpack"):
            return all(p.exists() and p.stat().st_size == f["unpack"][m] for m, p in self.unpacked(f).items())
        p = self.cfg.path(f["path"])
        return p.exists() and p.stat().st_size == f["size"]

    def unpacked(self, f):
        """{member: where it goes} for a package: next to the package, under the member's own name."""
        folder = self.cfg.path(f["path"]).parent
        return {m: folder / m.rsplit("/", 1)[-1] for m in f.get("unpack", {})}

    def package_ready(self, f):
        """A package that was downloaded and verified but not yet unpacked (e.g. the app was closed)."""
        p = self.cfg.path(f["path"])
        return bool(f.get("unpack")) and p.exists() and p.stat().st_size == f["size"]

    def converted(self, item):
        """The faster-whisper model made from a downloaded checkpoint (its source files are deleted then)."""
        folder = self.cfg.path(item["convert"]["to"]) if item.get("convert") else None
        return folder if folder and (folder / "model.bin").exists() else None

    def status(self, item_id):
        item = self.cfg.download_items()[item_id]
        # the state before the files: a download puts its files in place and says "done" after, so one that
        # ends meanwhile shows as still running, never as done with its files missing
        job = dict(self.jobs.get(item_id) or {})
        files, converted = item["files"], self.converted(item)
        missing = [] if converted else [f for f in files if not self.installed(f)]
        partial = sum(self.part_size(f) for f in missing)
        if converted:
            on_disk = sum(p.stat().st_size for p in converted.iterdir() if p.is_file())
        else:
            on_disk = sum(sum(f["unpack"].values()) if f.get("unpack") else f["size"] for f in files)
        return {"size": sum(f["size"] for f in files),
                "missing": sum(0 if self.package_ready(f) else f["size"] for f in missing), "on_disk": on_disk,
                "installed": bool(converted) if item.get("convert") else not missing, "partial": partial, **job}

    def all_status(self):
        return {item_id: self.status(item_id) for item_id in self.cfg.download_items()}

    def part_size(self, f):
        try:
            return self.part_path(f).stat().st_size
        except FileNotFoundError:  # not started, or put in place since it was looked for
            return 0

    def part_path(self, f):
        p = self.cfg.path(f["path"])
        return p.with_name(p.name + ".part")

    def busy(self, item_id):
        return (self.jobs.get(item_id) or {}).get("state") in ("downloading", "verifying", "unpacking", "converting")

    # ---- actions ---------------------------------------------------------------------------------
    def start(self, item_ids):
        """Download the given items (e.g. a model and the voiceprint model) one after another."""
        items = self.cfg.download_items()
        todo = [i for i in item_ids if i in items and not self.busy(i) and not self.status(i)["installed"]]
        unpack = sum(sum(f["unpack"].values()) for i in todo for f in items[i]["files"]
                     if f.get("unpack") and not self.installed(f))
        unpack += sum(items[i]["convert"].get("size", 0) for i in todo if items[i].get("convert"))  # the converted copy
        need = sum(self.status(i)["missing"] - self.status(i)["partial"] for i in todo) + unpack
        root = self.cfg.home
        root.mkdir(parents=True, exist_ok=True)
        if need and shutil.disk_usage(root).free < need + 500e6:
            raise OSError(f"not enough free disk space: {need / 1e9:.1f} GB needed")
        for i in todo:
            self.cancelled.discard(i)
            self.jobs[i] = {"state": "queued", "done": self.status(i)["partial"], "total": self.status(i)["missing"],
                            "error": None}
        if todo:
            threading.Thread(target=self.run, args=(todo,), daemon=True, name="sedjem-download").start()
        return todo

    def cancel(self, item_id):
        self.cancelled.add(item_id)
        job = self.jobs.get(item_id)
        if job and job["state"] == "queued":
            job["state"] = "cancelled"

    def remove(self, item_id):
        """Delete an item's files (and any partial download), and the folder they were in once it is empty."""
        if self.busy(item_id):
            raise RuntimeError("wait until the download has finished or cancel it")
        item = self.cfg.download_items()[item_id]
        for f in item["files"]:
            for p in (self.cfg.path(f["path"]), self.part_path(f)):
                p.unlink(missing_ok=True)
            for p in self.unpacked(f).values():
                p.unlink(missing_ok=True)
                p.with_name(p.name + ".part").unlink(missing_ok=True)
        for folder in {self.cfg.path(f["path"]).parent for f in item["files"]}:  # e.g. Nemotron-3-Diarization/
            try:
                folder.rmdir()
            except OSError:  # other files are still in it, or it is in use
                pass
        if item.get("convert"):  # the converted model, a conversion cut short, and the conversion's log
            for folder in (item["convert"]["to"], item["convert"]["to"] + ".part", item["convert"]["from"]):
                shutil.rmtree(self.cfg.path(folder), ignore_errors=True)
        self.jobs.pop(item_id, None)

    # ---- work --------------------------------------------------------------------------------------
    def run(self, item_ids):
        for item_id in item_ids:
            job = self.jobs[item_id]
            if item_id in self.cancelled:
                job["state"] = "cancelled"
                continue
            job["state"] = "downloading"
            try:
                item = self.cfg.download_items()[item_id]
                for f in item["files"]:
                    if not self.installed(f):
                        job["file"] = f["path"].rsplit("/", 1)[-1]
                        if not self.package_ready(f):
                            self.fetch(f, job, lambda: item_id in self.cancelled)
                        if f.get("unpack"):
                            self.unpack(f, job, lambda: item_id in self.cancelled)
                if item.get("convert"):
                    from .hub import convert  # in the model worker process: it needs transformers and torch
                    job.update(state="converting", file=None)
                    convert(self.cfg, item["convert"], lambda: item_id in self.cancelled)
                job.update(state="done", file=None)
                self.finished(item_id)
            except Cancelled:
                job.update(state="cancelled", file=None)
            except Exception as e:  # shown next to the model in the app
                job.update(state="error", error=str(e) or type(e).__name__, file=None)

    def finished(self, item_id):
        try:
            self.on_done(item_id)
        except Exception:  # noqa: BLE001 — counting is never a reason for a download to fail
            pass

    def fetch(self, f, job, cancelled):
        dest, part = self.cfg.path(f["path"]), self.part_path(f)
        dest.parent.mkdir(parents=True, exist_ok=True)
        failures = 0
        while True:
            have = part.stat().st_size if part.exists() else 0
            if have > f["size"]:  # not what we expected: start over
                part.unlink()
                have = 0
            if have == f["size"]:
                break
            try:
                headers = {"Accept-Encoding": "identity"}  # byte ranges must refer to the file itself
                if have:
                    headers["Range"] = f"bytes={have}-"
                with requests.get(f["url"], headers=headers, stream=True, timeout=(20, 60)) as r:
                    if have and r.status_code == 200:  # the server ignored the range: restart this file
                        job["done"] -= have
                        have = 0
                    elif r.status_code not in (200, 206):
                        if 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                            raise RuntimeError(f"HTTP {r.status_code} for {f['url']}")
                        r.raise_for_status()
                    with open(part, "ab" if have else "wb") as out:
                        for chunk in r.iter_content(1 << 20):
                            if cancelled():
                                raise Cancelled()
                            out.write(chunk)
                            job["done"] += len(chunk)
                failures = 0
            except (requests.RequestException, ConnectionError, TimeoutError) as e:
                failures += 1
                if failures > 8:
                    raise RuntimeError(f"download kept failing: {e}")
                for _ in range(min(30, 2 ** failures) * 2):  # back off, but notice a cancel
                    if cancelled():
                        raise Cancelled()
                    time.sleep(0.5)
        job["state"] = "verifying"
        # git's blob id is the SHA-1 of "blob <size>\0" and the content
        h = hashlib.sha256() if f.get("sha256") else hashlib.sha1(b"blob %d\0" % f["size"])
        with open(part, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 22), b""):
                h.update(block)
                if cancelled():
                    raise Cancelled()
        if h.hexdigest() != (f.get("sha256") or f["git_sha1"]):
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{dest.name}: checksum mismatch (the download was damaged; try again)")
        replace_file(part, dest)
        job["state"] = "downloading"

    def unpack(self, f, job, cancelled):
        """Copy the listed members out of a downloaded package, check their sizes, delete the package."""
        job["state"] = "unpacking"
        package = self.cfg.path(f["path"])
        with zipfile.ZipFile(package) as z:
            for member, dest in self.unpacked(f).items():
                tmp = dest.with_name(dest.name + ".part")
                try:
                    with z.open(member) as src, open(tmp, "wb") as out:
                        while block := src.read(1 << 22):
                            if cancelled():
                                raise Cancelled()
                            out.write(block)
                    if tmp.stat().st_size != f["unpack"][member]:
                        raise RuntimeError(f"{dest.name}: not the expected size in {package.name}")
                    if os.name != "nt" and z.getinfo(member).external_attr >> 16 & 0o111:
                        os.chmod(tmp, 0o755)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                replace_file(tmp, dest)
        package.unlink(missing_ok=True)
        job["state"] = "downloading"
