"""Tests for adding models from Hugging Face (app/hub.py): links, the GGUF header reader, what each kind of
repository gives and what is refused, git blob checksums in downloads, the conversion step, saved entries,
and the HTTP API. Hugging Face is replaced by a local stand-in, so nothing leaves this computer.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import transcribe  # noqa: E402
from app import hub  # noqa: E402
from app.config import Config  # noqa: E402
from app.downloads import Downloads  # noqa: E402
from app.engines import local_command  # noqa: E402
from app.server import make_server  # noqa: E402

SHA = "1234567890abcdef1234567890abcdef12345678"  # the one commit of every stand-in repository


def pack_string(b):
    return struct.pack("<Q", len(b)) + b


def gguf_header(arch, before=()):
    """The start of a GGUF file: header and keys, with (key, type, packed value) pairs ahead of general.architecture."""
    keys = [pack_string(k) + struct.pack("<I", t) + v for k, t, v in before]
    keys.append(pack_string(b"general.architecture") + struct.pack("<I", 8) + pack_string(arch.encode()))
    return b"GGUF" + struct.pack("<IQQ", 3, 7, len(keys)) + b"".join(keys)


def string_array(items):
    return struct.pack("<IQ", 8, len(items)) + b"".join(pack_string(x) for x in items)


def git_sha1(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


CT2_CONFIG = json.dumps({"alignment_heads": [[1, 0]], "lang_ids": [50259], "suppress_ids": [1], "suppress_ids_begin": [2]}).encode()
BIG_HEADER = gguf_header("canary", [(b"general.name", 8, pack_string(b"big")),
                                    (b"tokenizer.ggml.tokens", 9, string_array([b"token-%05d" % i for i in range(9000)])),
                                    (b"general.file_type", 4, struct.pack("<I", 15))])
# repo: (extra fields of the API record, {path: content}, paths stored with LFS)
REPOS = {
    "org/whisper-ct2": ({"cardData": {"license": "mit"}},
                        {"config.json": CT2_CONFIG, "vocabulary.json": b'["a", "b"]', "model.bin": b"M" * 5000,
                         "README.md": b"hello"}, {"model.bin"}),
    "org/whisper-ct2-full": ({}, {"config.json": CT2_CONFIG, "vocabulary.txt": b"a\nb\n", "tokenizer.json": b"{}",
                                  "preprocessor_config.json": b'{"feature_size": 80}', "model.bin": b"M" * 3000}, {"model.bin"}),
    "org/asr-gguf": ({"tags": ["asr", "license:apache-2.0"]},
                     {"asr-F16.gguf": gguf_header("parakeet") + b"\0" * 900, "asr-Q8_0.gguf": gguf_header("parakeet") + b"\1" * 500,
                      "asr-Q4_K_M.gguf": gguf_header("parakeet") + b"\2" * 300, "mmproj-asr-f16.gguf": gguf_header("clip"),
                      "README.md": b"x"}, {"asr-Q4_K_M.gguf"}),
    "org/big-header-gguf": ({}, {"model.gguf": BIG_HEADER + b"\0" * 64}, set()),
    "org/llm-gguf": ({}, {"llm-Q4_K_M.gguf": gguf_header("llama")}, set()),
    "org/whisper-hf": ({"safetensors": {"total": 1000}},
                       {"config.json": json.dumps({"model_type": "whisper"}).encode(), "model.safetensors": b"W" * 4000,
                        "vocab.json": b"{}", "merges.txt": b"#version: 0.2\n", "tokenizer_config.json": b"{}",
                        "training_args.bin": b"t", "README.md": b"r"}, {"model.safetensors", "training_args.bin"}),
    "org/wav2vec": ({}, {"config.json": json.dumps({"model_type": "wav2vec2"}).encode(), "model.safetensors": b"W"}, set()),
    "org/gated": ({"gated": "manual"}, {"model.bin": b""}, set()),
    "org/lora": ({}, {"adapter_config.json": b"{}", "adapter_model.safetensors": b"A"}, set()),
    "org/nothing": ({}, {"README.md": b"hello"}, set()),
}


class FakeHub(BaseHTTPRequestHandler):
    """Hugging Face's model API and file downloads for REPOS: paged file lists, Range requests."""
    ranges = []

    def log_message(self, *a):
        pass

    def send(self, status, body, ctype="application/json", headers=()):
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/") if p]
        if parts[:2] == ["api", "models"] and len(parts) == 6:
            repo = "/".join(parts[2:4])
            if repo not in REPOS:  # what Hugging Face says for a missing or private repository
                return self.send(401, {"error": "Invalid username or password."})
            extra, files, lfs = REPOS[repo]
            sha = extra.get("sha", SHA)
            if parts[4] == "revision":
                if parts[5] not in ("main", sha):
                    return self.send(404, {"error": f"Invalid rev id: {parts[5]}"})
                return self.send(200, {"id": repo, "sha": sha, "private": False, "gated": False, **extra})
            if parts[4] == "tree" and parts[5] == sha:
                entries = [self.entry(p, data, p in lfs) for p, data in files.items()]
                page, headers = urllib.parse.parse_qs(url.query).get("cursor", ["0"])[0], []
                if page == "0" and len(entries) > 3:  # two pages, as Hugging Face sends long listings
                    next_url = f"{hub.BASE}/api/models/{repo}/tree/{sha}?recursive=true&cursor=1"
                    headers, entries = [("Link", f'<{next_url}>; rel="next"')], entries[:3]
                elif page == "1":
                    entries = entries[3:]
                return self.send(200, entries + [{"type": "directory", "path": "sub", "oid": "0" * 40, "size": 0}],
                                 headers=headers)
        repo = REPOS.get("/".join(parts[:2]))
        if len(parts) >= 5 and repo and parts[2:4] == ["resolve", repo[0].get("sha", SHA)]:
            data = repo[1].get("/".join(parts[4:]))
            if data is None:
                return self.send(404, {"error": "Entry not found"})
            rng = self.headers.get("Range")
            if not rng:
                return self.send(200, data, "application/octet-stream")
            self.ranges.append(("/".join(parts[4:]), rng))
            start, end = rng.split("=")[1].split("-")
            start, end = int(start), min(int(end or len(data) - 1), len(data) - 1)
            return self.send(206, data[start:end + 1], "application/octet-stream",
                             [("Content-Range", f"bytes {start}-{end}/{len(data)}")])
        self.send(404, {"error": "not found"})

    @staticmethod
    def entry(path, data, lfs):
        e = {"type": "file", "path": path, "size": len(data), "oid": git_sha1(data)}
        if lfs:  # the git id is then the pointer file's, and the content's SHA-256 is in "lfs"
            e.update(oid=git_sha1(b"version https://git-lfs.github.com/spec/v1\n"),
                     lfs={"oid": hashlib.sha256(data).hexdigest(), "size": len(data), "pointerSize": 130})
        return e


def setUpModule():
    global FAKE, BASE_PATCH
    FAKE = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
    threading.Thread(target=FAKE.serve_forever, daemon=True).start()
    BASE_PATCH = mock.patch.object(hub, "BASE", f"http://127.0.0.1:{FAKE.server_address[1]}")
    BASE_PATCH.start()


def tearDownModule():
    BASE_PATCH.stop()
    FAKE.shutdown()
    FAKE.server_close()


def temp_config(add_cleanup):
    """A Config on an empty data folder, without the speaker models' downloads (TitaNet-small's would come from GitHub)."""
    home = Path(tempfile.mkdtemp(prefix="sedjem-hub-"))
    add_cleanup(shutil.rmtree, home, ignore_errors=True)
    (home / "app_data").mkdir()
    (home / "app_data" / "config.toml").write_text("[local]\nspeaker_models = []\n")
    return Config(home=home)


def wait_download(dl, item_id, until=("done", "error", "cancelled"), timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        job = dl.jobs.get(item_id) or {}
        if job.get("state") in until:
            return job
        time.sleep(0.05)
    raise AssertionError(f"download still {dl.jobs.get(item_id)}")


class LinkTests(unittest.TestCase):
    def test_links_and_names(self):
        for link, want in [
            ("org/name", ("org/name", None, None)),
            ("  https://huggingface.co/org/name  ", ("org/name", None, None)),
            ("huggingface.co/org/name/", ("org/name", None, None)),
            ("https://hf.co/Org_1/my.model-v2?library=transformers#files", ("Org_1/my.model-v2", None, None)),
            ("https://huggingface.co/org/name/tree/main", ("org/name", "main", None)),
            ("https://huggingface.co/org/name/tree/v1.0/ct2", ("org/name", "v1.0", "ct2")),
            ("https://huggingface.co/org/name/tree/refs%2Fpr%2F3", ("org/name", "refs/pr/3", None)),
            ("https://huggingface.co/org/name/blob/abc123/model-Q4_K_M.gguf", ("org/name", "abc123", "model-Q4_K_M.gguf")),
            ("https://huggingface.co/org/name/resolve/main/sub/m.gguf?download=true", ("org/name", "main", "sub/m.gguf")),
            ("https://huggingface.co/org/name/commit/" + SHA, ("org/name", SHA, None)),
            ("https://huggingface.co/org/name/discussions/4", ("org/name", None, None)),
        ]:
            self.assertEqual(hub.parse_link(link), want, link)

    def test_refused_links(self):
        for link, words in [
            ("", "Paste"), ("whisper-small", "two parts"), ("https://example.com/org/name", "isn't a Hugging Face link"),
            ("ftp://huggingface.co/org/name", "isn't a Hugging Face link"),
            ("https://huggingface.co/datasets/org/name", "dataset"), ("https://huggingface.co/spaces/org/app", "Space"),
            ("org/na me", "isn't a Hugging Face model name"), ("org/..", "isn't a Hugging Face model name"),
            ("org--x/name", "isn't a Hugging Face model name"), ("-org/name", "isn't a Hugging Face model name"),
            ("https://huggingface.co/org/name/blob/main/../../etc/passwd", "isn't a usable file name"),
            ("https://huggingface.co/org/name/blob/main/C:%5Cx.gguf", "isn't a usable file name"),
            ("https://huggingface.co/org/name/tree/..", "isn't a branch"),
            ("https://huggingface.co/org/name/blob", "cut short"),
        ]:
            with self.assertRaises(hub.HubError, msg=link) as e:
                hub.parse_link(link)
            self.assertIn(words, str(e.exception), link)

    def test_file_paths(self):
        self.assertEqual(hub.check_path("sub/model.gguf"), "sub/model.gguf")
        for bad in ("/etc/passwd", "a/../b", "a//b", "a\\b", "c:x", "a\nb", ""):
            with self.assertRaises(hub.HubError, msg=bad):
                hub.check_path(bad)


class GgufHeaderTests(unittest.TestCase):
    def test_architecture_first(self):
        self.assertEqual(hub.gguf_architecture(gguf_header("cohere_asr")), "cohere_asr")

    def test_architecture_after_other_keys(self):
        before = [(b"general.alignment", 4, struct.pack("<I", 32)), (b"general.name", 8, pack_string(b"x")),
                  (b"tokens", 9, string_array([b"a", b"bc"])), (b"scores", 9, struct.pack("<IQ", 6, 2) + b"\0" * 8),
                  (b"flag", 7, b"\1"), (b"size", 10, struct.pack("<Q", 5))]
        self.assertEqual(hub.gguf_architecture(gguf_header("whisper", before)), "whisper")

    def test_truncated_bad_and_missing(self):
        data = gguf_header("parakeet", [(b"general.name", 8, pack_string(b"a long enough name"))])
        for n in (4, 12, 30, len(data) - 3):  # cut anywhere after the magic: more bytes are needed
            with self.assertRaises(hub.Truncated, msg=n):
                hub.gguf_architecture(data[:n])
        with self.assertRaises(ValueError) as e:
            hub.gguf_architecture(b"PK\3\4" + data[4:])
        self.assertNotIsInstance(e.exception, hub.Truncated)
        no_arch = b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + pack_string(b"general.name") + struct.pack("<I", 8) + pack_string(b"x")
        with self.assertRaises(ValueError):
            hub.gguf_architecture(no_arch)
        with self.assertRaises(ValueError):
            hub.gguf_architecture(b"GGUF" + struct.pack("<IQQ", 1, 0, 0))


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_config(self.addCleanup)

    def refused(self, link, words, **kw):
        with self.assertRaises(hub.HubError) as e:
            hub.find(self.cfg, link, **kw)
        self.assertIn(words, str(e.exception))

    def test_faster_whisper_gets_the_pinned_tokenizer(self):
        found = hub.find(self.cfg, "https://huggingface.co/org/whisper-ct2")
        self.assertEqual((found["kind"], found["architecture"], found["revision"], found["licence"]),
                         ("ct2", "whisper", SHA, "MIT"))
        files = {f["path"].rsplit("/", 1)[1]: f for f in found["files"]}
        self.assertEqual(list(files), ["config.json", "vocabulary.json", "tokenizer.json", "model.bin"])  # model.bin last
        self.assertTrue(all(f["path"].startswith("models/hf/org--whisper-ct2/") for f in found["files"]))
        self.assertEqual(files["config.json"]["git_sha1"], git_sha1(CT2_CONFIG))
        self.assertEqual(files["model.bin"]["sha256"], hashlib.sha256(b"M" * 5000).hexdigest())
        self.assertEqual(files["model.bin"]["url"], f"{hub.BASE}/org/whisper-ct2/resolve/{SHA}/model.bin")
        self.assertEqual({k: files["tokenizer.json"][k] for k in ("url", "size", "sha256")}, hub.WHISPER_TOKENIZER)

        m = hub.entry(found)
        self.assertEqual((m["id"], m["engine"], m["whisper_model"], m["title"], m["prompt"]),
                         ("hf-org--whisper-ct2", "whisper", "models/hf/org--whisper-ct2", "whisper-ct2", True))
        self.assertEqual(m["facts"], ["org/whisper-ct2", "faster-whisper (CTranslate2)", "2.5 MB download", "Licence: MIT"])
        self.assertEqual(m["hub"], {"repo": "org/whisper-ct2", "revision": SHA, "kind": "ct2",
                                    "label": "faster-whisper (CTranslate2)", "file": None, "architecture": "whisper"})
        self.assertGreater(m["rtf"], 0)

    def test_faster_whisper_with_its_own_tokenizer(self):
        found = hub.find(self.cfg, "org/whisper-ct2-full")  # model.bin is on the second page of the file list
        names = [f["path"].rsplit("/", 1)[1] for f in found["files"]]
        self.assertEqual(names, ["config.json", "vocabulary.txt", "tokenizer.json", "preprocessor_config.json", "model.bin"])
        self.assertTrue(all(f["url"].startswith(hub.BASE) for f in found["files"]), "all from the repository itself")

    def test_gguf_picks_a_file_and_reads_its_family(self):
        FakeHub.ranges.clear()
        found = hub.find(self.cfg, "org/asr-gguf")
        self.assertEqual((found["kind"], found["architecture"], found["file"]), ("gguf", "parakeet", "asr-Q4_K_M.gguf"))
        self.assertEqual([c["file"] for c in found["choices"]], ["asr-Q4_K_M.gguf", "asr-Q8_0.gguf", "asr-F16.gguf"])
        self.assertEqual(FakeHub.ranges, [("asr-Q4_K_M.gguf", "bytes=0-65535")], "only the header is read")
        self.assertEqual(found["files"][0]["sha256"], hashlib.sha256(REPOS["org/asr-gguf"][1]["asr-Q4_K_M.gguf"]).hexdigest())
        self.assertEqual(found["licence"], "Apache-2.0")
        m = hub.entry(found)
        self.assertEqual((m["engine"], m["cohere_model"], m["prompt"]),
                         ("gguf", "models/hf/org--asr-gguf/asr-Q4_K_M.gguf", False))
        self.assertIn("rtf_gpu", m)
        self.assertEqual(m["tagline"], "NVIDIA Parakeet model")

        self.assertEqual(hub.find(self.cfg, "org/asr-gguf", file="asr-Q8_0.gguf")["file"], "asr-Q8_0.gguf")
        link = f"https://huggingface.co/org/asr-gguf/blob/{SHA}/asr-F16.gguf"
        self.assertEqual(hub.find(self.cfg, link)["file"], "asr-F16.gguf")
        self.assertEqual(hub.find(self.cfg, "org/asr-gguf", file="mmproj-asr-f16.gguf")["file"], "asr-Q4_K_M.gguf",
                         "a projector file isn't a model: the usual choice instead")

    def test_gguf_header_further_in(self):
        FakeHub.ranges.clear()
        self.assertGreater(len(BIG_HEADER), 1 << 16)
        self.assertEqual(hub.find(self.cfg, "org/big-header-gguf")["architecture"], "canary")
        self.assertEqual([r for _, r in FakeHub.ranges], ["bytes=0-65535", "bytes=0-1048575"])

    def test_transformers_whisper(self):
        with mock.patch.object(hub, "can_convert", return_value=True):
            found = hub.find(self.cfg, "org/whisper-hf")
        self.assertEqual((found["kind"], found["problem"]), ("transformers", None))
        names = [f["path"] for f in found["files"]]
        self.assertTrue(all(n.startswith("models/hf/org--whisper-hf-src/") for n in names))
        self.assertEqual([n.rsplit("/", 1)[1] for n in names],
                         ["config.json", "tokenizer_config.json", "vocab.json", "merges.txt", "model.safetensors"])
        m = hub.entry(found)
        self.assertEqual((m["engine"], m["whisper_model"]), ("whisper", "models/hf/org--whisper-hf"))
        self.assertEqual(m["convert"], {"from": "models/hf/org--whisper-hf-src", "to": "models/hf/org--whisper-hf", "size": 4000})

        with mock.patch.object(hub, "can_convert", return_value=False):
            self.assertEqual(hub.inspect(self.cfg, "org/whisper-hf")["problem"], hub.CANT_CONVERT)
            with self.assertRaises(hub.HubError) as e:
                hub.add(self.cfg, "org/whisper-hf")
        self.assertIn("run Sedjem from source with transformers and torch", str(e.exception))
        self.assertIn("faster-whisper (CTranslate2) version", str(e.exception))

    def test_refusals(self):
        self.refused("org/llm-gguf", "“llama”, which transcribe.cpp can't run")
        self.refused("org/wav2vec", "type “wav2vec2”")
        self.refused("org/gated", "gated")
        self.refused("org/lora", "adapter")
        self.refused("org/nothing", "didn't find a model")
        self.refused("someone/private-model", "no public model called someone/private-model")
        self.refused("https://huggingface.co/org/asr-gguf/tree/nope", "no branch, tag or commit called “nope”")
        self.refused("https://huggingface.co/org/asr-gguf/tree/main/missing", "no file or folder called missing")

    def test_no_network(self):
        with mock.patch.object(hub, "BASE", "http://127.0.0.1:9"):  # nothing listens there
            self.refused("org/asr-gguf", "Couldn't reach Hugging Face")

    def test_ids_never_clash(self):
        self.cfg.models = {"hf-org--a-b": {"hub": {"repo": "org/a.b"}}, "hf-x--y": {"id": "hf-x--y"}}
        self.assertEqual(hub.model_id(self.cfg, "Org/A.B"), "hf-org--a-b", "the same repository keeps its id")
        other = hub.model_id(self.cfg, "org/a-b")
        self.assertTrue(other.startswith("hf-org--a-b-") and other != "hf-org--a-b")
        self.assertNotEqual(hub.model_id(self.cfg, "x/y"), "hf-x--y", "a model from the config keeps its id")


class SavedModelTests(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_config(self.addCleanup)

    def test_add_load_and_forget(self):
        m = hub.add(self.cfg, "org/asr-gguf", file="asr-Q8_0.gguf")
        self.assertIn(m["id"], self.cfg.models)
        saved = json.loads(self.cfg.added_models_path.read_text())["models"]
        self.assertEqual([x["id"] for x in saved], ["hf-org--asr-gguf"])
        with self.assertRaises(hub.HubError):
            hub.add(self.cfg, "org/asr-gguf")  # already there
        self.assertTrue(hub.inspect(self.cfg, "org/asr-gguf")["added"])

        again = Config(home=self.cfg.home)  # the next start
        self.assertEqual(again.models["hf-org--asr-gguf"]["cohere_model"], "models/hf/org--asr-gguf/asr-Q8_0.gguf")
        self.assertEqual(list(again.models)[-1], "hf-org--asr-gguf", "after the models of the config")
        self.assertEqual(again.availability(again.models["hf-org--asr-gguf"]), (False, "not downloaded"))
        self.assertIn("hf-org--asr-gguf", again.download_items())

        folder = self.cfg.path("models/hf/org--asr-gguf")
        folder.mkdir(parents=True)
        (folder / "asr-Q8_0.gguf").write_bytes(b"x")
        hub.forget(self.cfg, m["id"])
        self.assertNotIn(m["id"], self.cfg.models)
        self.assertFalse(folder.exists())
        self.assertEqual(json.loads(self.cfg.added_models_path.read_text())["models"], [])

    def test_saved_entries_never_replace_the_config(self):
        entry = {"kind": "local", "engine": "gguf", "title": "x", "files": [], "hub": {"repo": "a/b"}}
        self.cfg.added_models_path.write_text(json.dumps({"models": [
            {**entry, "id": "whisper-medium"}, {**entry, "id": "not-from-hub"}, {**entry, "id": "hf-ok", "cohere_model": "m"},
            {**entry, "id": "hf-no-hub", "hub": None}, "junk"]}))
        cfg = Config(home=self.cfg.home)
        self.assertEqual(cfg.models["whisper-medium"]["engine"], "whisper")
        self.assertNotIn("not-from-hub", cfg.models)
        self.assertNotIn("hf-no-hub", cfg.models)
        self.assertIn("hf-ok", cfg.models)

    def test_forget_stays_inside_models_hf(self):
        outside = self.cfg.path("models/keep")
        outside.mkdir(parents=True)
        m = {"id": "hf-bad", "kind": "local", "engine": "whisper", "whisper_model": "models/keep", "title": "x",
             "files": [{"path": "../../elsewhere/model.bin"}], "hub": {"repo": "a/b"}}
        self.cfg.models = {**self.cfg.models, m["id"]: m}
        hub.forget(self.cfg, m["id"])
        self.assertTrue(outside.exists())


class DownloadTests(unittest.TestCase):
    """The downloader with Hugging Face's checksums, and the conversion step."""

    def setUp(self):
        self.cfg = temp_config(self.addCleanup)

    def use(self, m):
        self.cfg.models = {m["id"]: m}
        return m["id"]

    def test_git_blob_checksums(self):
        found = hub.find(self.cfg, "org/whisper-ct2-full")
        mid = self.use(hub.entry(found))
        self.assertEqual([("git_sha1" in f, "sha256" in f) for f in found["files"]],
                         [(True, False)] * 4 + [(False, True)])
        dl = Downloads(self.cfg)
        dl.start([mid])
        self.assertEqual(wait_download(dl, mid)["state"], "done")
        self.assertTrue(dl.status(mid)["installed"])
        self.assertEqual(self.cfg.availability(self.cfg.models[mid]), (True, None))
        self.assertEqual(self.cfg.path(found["files"][1]["path"]).read_bytes(), b"a\nb\n")

        bad = hub.entry(found)
        bad["files"] = [{**bad["files"][0], "git_sha1": "0" * 40, "path": "models/hf/bad/config.json"}]
        mid = self.use({**bad, "id": "hf-bad"})
        dl.start([mid])
        job = wait_download(dl, mid)
        self.assertEqual(job["state"], "error")
        self.assertIn("checksum mismatch", job["error"])
        self.assertFalse(self.cfg.path("models/hf/bad/config.json").exists())

    def test_conversion_step(self):
        with mock.patch.object(hub, "can_convert", return_value=True):
            m = hub.entry(hub.find(self.cfg, "org/whisper-hf"))
        mid = self.use(m)
        src, dst = self.cfg.path(m["convert"]["from"]), self.cfg.path(m["convert"]["to"])
        dl, seen = Downloads(self.cfg), []

        def fake_convert(cfg, spec, cancelled):
            seen.append((dl.jobs[mid]["state"], sorted(p.name for p in src.iterdir())))
            dst.mkdir(parents=True)
            (dst / "model.bin").write_bytes(b"c" * 10)
            shutil.rmtree(src)

        with mock.patch.object(hub, "convert", fake_convert):
            dl.start([mid])
            self.assertEqual(wait_download(dl, mid)["state"], "done")
        self.assertEqual(seen, [("converting", ["config.json", "merges.txt", "model.safetensors", "tokenizer_config.json",
                                                "vocab.json"])])
        status = Downloads(self.cfg).status(mid)  # after a restart too: the source files are gone
        self.assertTrue(status["installed"])
        self.assertEqual((status["missing"], status["on_disk"]), (0, 10))
        self.assertEqual(self.cfg.availability(m), (True, None))
        dl.remove(mid)
        self.assertFalse(dst.exists())
        self.assertFalse(dl.status(mid)["installed"])

    def test_failed_conversion(self):
        with mock.patch.object(hub, "can_convert", return_value=True):
            mid = self.use(hub.entry(hub.find(self.cfg, "org/whisper-hf")))
        dl = Downloads(self.cfg)
        with mock.patch.object(hub, "convert", side_effect=RuntimeError("the conversion failed: out of memory")):
            dl.start([mid])
            job = wait_download(dl, mid)
        self.assertEqual((job["state"], job["error"]), ("error", "the conversion failed: out of memory"))
        status = dl.status(mid)
        self.assertEqual((status["installed"], status["missing"]), (False, 0), "downloaded, not converted yet")

    @unittest.skipUnless(all(importlib.util.find_spec(m) for m in hub.CONVERT_NEEDS), "needs transformers and torch")
    def test_real_conversion_of_a_tiny_checkpoint(self):
        """The model worker's --convert-whisper on a made-up two-layer Whisper with a byte-level tokenizer and
        no tokenizer.json or preprocessor_config.json, as many fine-tunes on Hugging Face are shared."""
        import transformers
        transformers.utils.logging.disable_progress_bar()
        spec = {"from": "models/hf/t--tiny-src", "to": "models/hf/t--tiny"}
        src = self.cfg.path(spec["from"])
        src.mkdir(parents=True)
        keep = [*range(33, 127), *range(161, 173), *range(174, 256)]  # GPT-2's stand-ins for the 256 byte values
        extra = iter(range(256, 512))
        vocab = {chr(b) if b in keep else chr(next(extra)): b for b in range(256)}
        specials = ["<|endoftext|>", "<|startoftranscript|>", "<|en|>", "<|translate|>", "<|transcribe|>",
                    "<|startoflm|>", "<|startofprev|>", "<|nocaptions|>", "<|notimestamps|>"]
        ids = {t: 256 + i for i, t in enumerate(specials)}
        for name, data in (("vocab.json", vocab), ("added_tokens.json", ids), ("tokenizer_config.json", {"tokenizer_class": "WhisperTokenizer"}),
                           ("special_tokens_map.json", {"eos_token": specials[0], "additional_special_tokens": specials[1:]})):
            (src / name).write_text(json.dumps(data))
        (src / "merges.txt").write_text("#version: 0.2\n")
        eot = ids["<|endoftext|>"]
        config = transformers.WhisperConfig(
            vocab_size=len(vocab) + len(ids) + 20, d_model=16, encoder_layers=1, decoder_layers=1, encoder_attention_heads=2,
            decoder_attention_heads=2, encoder_ffn_dim=32, decoder_ffn_dim=32, max_target_positions=64,
            decoder_start_token_id=ids["<|startoftranscript|>"], pad_token_id=eot, bos_token_id=eot, eos_token_id=eot)
        transformers.WhisperForConditionalGeneration(config).save_pretrained(src)
        hub.convert(self.cfg, spec, lambda: False)
        dst = self.cfg.path(spec["to"])
        self.assertEqual(sorted(p.name for p in dst.iterdir()),
                         ["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"])
        self.assertFalse(src.exists(), "the download is deleted once converted")
        from faster_whisper import WhisperModel
        WhisperModel(str(dst), device="cpu", compute_type="int8", local_files_only=True)  # loads


def stand_in(c):
    """A small copy of what a catalog entry's repository holds, at its pinned revision."""
    if c["kind"] == "gguf":
        files = {f["file"]: gguf_header(c["architecture"]) + b"\0" * 64 for f in c["files"]}
        return {"sha": c["revision"]}, files, set(files)
    if c["kind"] == "ct2":
        files = {"config.json": CT2_CONFIG, "vocabulary.json": b"[]", "tokenizer.json": b"{}", "model.bin": b"M" * 100}
        return {"sha": c["revision"]}, files, {"model.bin"}
    files = {"config.json": json.dumps({"model_type": "whisper"}).encode(), "model.safetensors": b"W" * 100,
             "vocab.json": b"{}", "merges.txt": b"#version: 0.2\n"}
    return {"sha": c["revision"]}, files, {"model.safetensors"}


class CatalogTests(unittest.TestCase):
    """app/catalog.toml, the recommended models, each checked against a stand-in of its repository."""

    def setUp(self):
        self.cfg = temp_config(self.addCleanup)
        self.entries = hub.recommended()

    def test_entries_are_complete(self):
        keys = [c.get("builtin") or c["repo"] for c in self.entries]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue({"cohere", "whisper-medium", "large-v3"} <= set(keys))
        for c in self.entries:
            for field in ("name", "good_for", "evidence", "licence"):
                self.assertTrue(c.get(field), (c, field))
                self.assertNotIn("—", c[field], "plain sentences, no em dashes")
            self.assertTrue(c["evidence"].startswith("In the project's tests" if c.get("builtin") else "Not tested by the project"), c)
            self.assertIn(c["gpu"], ("any", "nvidia"))
            if c.get("builtin"):
                self.assertIn(c["builtin"], self.cfg.models)
                continue
            hub.check_repo(c["repo"])
            self.assertRegex(c["revision"], r"^[0-9a-f]{40}$")
            self.assertIn(c["kind"], ("ct2", "gguf", "transformers"))
            self.assertIn(c["architecture"], hub.FAMILIES)
            self.assertEqual(c["gpu"], "any" if c["kind"] == "gguf" else "nvidia")
            if c["kind"] == "gguf":
                self.assertTrue(c["files"])
                for f in c["files"]:
                    self.assertTrue(hub.check_path(f["file"]) and hub.is_gguf(f["file"]) and f["size"] > 0, f)
            else:
                self.assertGreater(c["size"], 0)

    def test_each_entry_resolves_to_a_model(self):
        hubbed = [c for c in self.entries if not c.get("builtin")]
        with mock.patch.dict(REPOS, {c["repo"]: stand_in(c) for c in hubbed}), \
                mock.patch.object(hub, "can_convert", return_value=True):
            for c in hubbed:
                for file in [f["file"] for f in c.get("files", [])] or [None]:
                    found = hub.find(self.cfg, c["repo"], file=file, revision=c["revision"])
                    self.assertEqual((found["kind"], found["architecture"], found["revision"], found["file"]),
                                     (c["kind"], c["architecture"], c["revision"], file), c["repo"])
                    m = hub.entry(found)
                    self.assertEqual(m["engine"], "gguf" if c["kind"] == "gguf" else "whisper")
                    self.assertTrue(m["files"] and all(f["path"].startswith("models/hf/") for f in m["files"]))
                    self.assertTrue(m["id"].startswith("hf-") and m["rtf"] > 0)

    def test_what_this_installation_has(self):
        cohere = next(c for c in self.entries if c.get("repo", "").endswith("cohere-transcribe-arabic-07-2026-gguf"))
        added = {"id": "hf-x", "kind": "local", "title": "x", "hub": {"repo": cohere["repo"].upper(), "file": cohere["files"][1]["file"]}}
        self.cfg.models = {k: v for k, v in self.cfg.models.items() if k != "large-v3"} | {"hf-x": added}
        with mock.patch.object(hub, "can_convert", return_value=False):
            items = {c["key"]: c for c in hub.catalog(self.cfg)}
        self.assertNotIn("large-v3", items, "a model hidden in the config isn't recommended either")
        self.assertEqual(items["cohere"]["builtin"], "cohere")
        self.assertEqual((items[cohere["repo"]]["added"], items[cohere["repo"]]["added_file"]), ("hf-x", cohere["files"][1]["file"]))
        converts = [c for c in items.values() if c.get("kind") == "transformers"]
        self.assertTrue(converts and all(c["problem"] == hub.NEEDS_SOURCE for c in converts))
        self.assertTrue(all(c["problem"] is None for c in items.values() if c.get("kind") != "transformers"))


class LanguageRetryTests(unittest.TestCase):
    """transcribe.py's GGUF engine: a family that refuses the language option runs without one."""

    class Session:
        def __init__(self, refuse):
            self.refuse, self.calls = refuse, []

        def run(self, audio, language=None):
            import transcribe_cpp
            self.calls.append(language)
            if self.refuse == "language" and language is not None:
                raise transcribe_cpp.errors.UnsupportedRequest("transcribe_run: unsupported language (status 10)")
            if self.refuse == "everything":
                raise transcribe_cpp.errors.InvalidArgument("bad audio")
            return type("Result", (), {"text": "hello"})()

    def engine(self, refuse):
        e = transcribe.Cohere.__new__(transcribe.Cohere)  # without loading a model
        e.name, e.language, e.device, e.session = "fake", "ar", "cpu", self.Session(refuse)
        return e

    def test_runs_without_the_language(self):
        e = self.engine("language")
        with mock.patch("sys.stderr"):
            self.assertEqual(e(np.zeros(16000, np.float32)), "hello")
        self.assertEqual(e(np.zeros(16000, np.float32)), "hello")
        self.assertEqual((e.session.calls, e.language), (["ar", None, None], None))

    def test_keeps_the_language_when_that_wasnt_it(self):
        import transcribe_cpp
        e = self.engine("everything")
        with mock.patch("sys.stderr"), self.assertRaises(transcribe_cpp.errors.InvalidArgument):
            e(np.zeros(16000, np.float32))
        self.assertEqual(e.language, "ar")

    def test_gguf_engine_and_command(self):
        self.assertIs(transcribe.ENGINES["gguf"], transcribe.Cohere)
        cfg = temp_config(self.addCleanup)
        m = {"id": "hf-x", "kind": "local", "engine": "gguf", "cohere_model": "models/hf/x--y/y.gguf"}
        cmd = local_command(cfg, m, "a.flac", "out", "p.json", {"speakers": "none", "language": "ar"})
        self.assertEqual(cmd[cmd.index("--engine") + 1], "gguf")
        self.assertEqual(cmd[cmd.index("--cohere-model") + 1], str(cfg.path("models/hf/x--y/y.gguf")))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = temp_config(cls.addClassCleanup)
        cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-Sedjem": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def model(self, mid):
        return next((m for m in self.call("GET", "/api/status")[1]["models"] if m["id"] == mid), None)

    def wait_ready(self, mid):
        end = time.time() + 30
        while time.time() < end:
            m = self.model(mid)
            if m and m["ready"]:
                return m
            time.sleep(0.1)
        self.fail(f"{mid} not ready: {self.model(mid)}")

    def test_inspect(self):
        path = self.cfg.added_models_path
        saved = path.read_text() if path.exists() else None
        status, found = self.call("POST", "/api/hub/inspect", {"url": "https://huggingface.co/org/asr-gguf"})
        self.assertEqual(status, 200, found)
        self.assertEqual((found["kind"], found["file"], found["revision"], found["added"]), ("gguf", "asr-Q4_K_M.gguf", SHA, False))
        self.assertEqual(found["size"], len(REPOS["org/asr-gguf"][1]["asr-Q4_K_M.gguf"]))
        self.assertEqual(self.call("POST", "/api/hub/inspect", {"url": "org/asr-gguf", "file": "asr-Q8_0.gguf"})[1]["file"],
                         "asr-Q8_0.gguf")
        status, body = self.call("POST", "/api/hub/inspect", {"url": "org/gated"})
        self.assertEqual(status, 400)
        self.assertIn("gated", body["error"])
        self.assertEqual(path.read_text() if path.exists() else None, saved, "a look saves nothing")

    def test_add_download_and_remove(self):
        mid = "hf-org--whisper-ct2-full"
        status, body = self.call("POST", "/api/hub/add", {"url": "org/whisper-ct2-full", "revision": SHA})
        self.assertEqual(status, 200, body)
        self.assertIn(mid, [m["id"] for m in body["models"]])
        m = self.wait_ready(mid)
        self.assertEqual(m["hub"]["repo"], "org/whisper-ct2-full")
        self.assertTrue(m["download"]["installed"])
        self.assertEqual(self.call("POST", "/api/hub/add", {"url": "org/whisper-ct2-full"})[0], 400, "already added")
        folder = self.cfg.path("models/hf/org--whisper-ct2-full")
        self.assertTrue((folder / "model.bin").exists())

        job = self.app.store.create({"id": self.app.store.new_id(), "title": "t", "created": "2026-09-25T10:00:00+03:00",
                                     "model": mid, "kind": "local", "status": "running"})
        self.addCleanup(self.app.store.delete, job["id"])
        self.assertEqual(self.call("DELETE", f"/api/models/{mid}")[0], 409, "a transcription is using it")
        self.app.store.update(job["id"], status="done")
        status, body = self.call("DELETE", f"/api/models/{mid}")
        self.assertEqual(status, 200, body)
        self.assertNotIn(mid, [m["id"] for m in body["models"]])
        self.assertFalse(folder.exists())
        self.assertNotIn(mid, [x["id"] for x in json.loads(self.cfg.added_models_path.read_text())["models"]])
        self.assertEqual(self.call("DELETE", f"/api/models/{mid}")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/models/hf-whatever")[0], 404)

    def test_add_gguf_runs_with_the_gguf_engine(self):
        mid = "hf-org--asr-gguf"
        status, body = self.call("POST", "/api/hub/add", {"url": "org/asr-gguf"})
        self.assertEqual(status, 200, body)
        m = self.wait_ready(mid)
        self.assertEqual((m["engine"], m["prompt"]), ("gguf", False))
        self.assertIn(self.app.runs_on(self.cfg.models[mid]), ("cpu", "gpu"))
        self.assertTrue(self.cfg.path("models/hf/org--asr-gguf/asr-Q4_K_M.gguf").exists())
        self.assertEqual(self.call("DELETE", f"/api/models/{mid}")[0], 200)

    def test_status_lists_the_recommended_models(self):
        catalog = self.call("GET", "/api/status")[1]["catalog"]
        self.assertEqual([c["key"] for c in catalog][:3], ["cohere", "whisper-medium", "large-v3"])
        self.assertTrue(all("added" in c and "problem" in c for c in catalog))

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/api/hub/add", {"url": "https://example.com/org/name"})[0], 400)
        self.assertEqual(self.call("POST", "/api/hub/inspect", {})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/models/whisper-medium")[0], 404, "only added models can be removed")


if __name__ == "__main__":
    unittest.main()
