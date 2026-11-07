#!/usr/bin/env python3
"""Resumable, parallel-range downloader for the GGUF model files.

On this network Hugging Face's CDN cancels long HTTP/2 streams and each
connection is slow, so every file is fetched as several byte ranges over
HTTP/1.1. Each range resumes after a failure. The ranges are then joined and
checked against the SHA-256 published on Hugging Face.

Usage: .venv/bin/python bench/fetch_models.py [repo/name:file ...]
  With no arguments it fetches the R2T2 and Qwen3-ASR files below; otherwise it
  looks up the size and SHA-256 of each given Hugging Face file and fetches it.
"""
import hashlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

MODELS = Path(__file__).resolve().parent.parent / "models"
PARTS = 8
FILES_AT_ONCE = 2
FILES = [  # (repo, filename, size, sha256), in download order
    ("netease-youdao/Confucius4-R2T2-GGUF", "mmproj-Confucius4-R2T2-Q8_0.gguf", 348336544,
     "8dc2c67e6a0484114928142d098db7ad94ae9f34c78948ef9d37a9678418cb65"),
    ("netease-youdao/Confucius4-R2T2-GGUF", "Confucius4-R2T2-Q8_0.gguf", 1834422208,
     "151097e43957a19984ea7de66e8144ce69b95039eb31c93da4f58db367e455c3"),
    ("ggml-org/Qwen3-ASR-1.7B-GGUF", "mmproj-Qwen3-ASR-1.7B-Q8_0.gguf", 355709344,
     "46c1d533af3f354ceb37ce855dbceff7da7fa7cf1e6a523df3b13440bd164c0d"),
    ("ggml-org/Qwen3-ASR-1.7B-GGUF", "Qwen3-ASR-1.7B-Q8_0.gguf", 2165034944,
     "58e22d0532d4eacaf034cfac17a6fed159f37c41390c710186783be439d1fc57"),
]

downloaded = 0
lock = threading.Lock()


def fetch_range(url, part, start, end):
    """Download bytes [start, end] (inclusive) into `part`, resuming until complete."""
    global downloaded
    want = end - start + 1
    while True:
        have = part.stat().st_size if part.exists() else 0
        if have >= want:
            return
        try:
            headers = {"Range": f"bytes={start + have}-{end}"}
            with requests.get(url, headers=headers, stream=True, timeout=(20, 60)) as r:
                if 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                    raise PermissionError(f"HTTP {r.status_code} for {url} (gated, private or wrong path?)")
                if r.status_code != 206:
                    raise RuntimeError(f"HTTP {r.status_code}")
                with open(part, "ab") as f:
                    for chunk in r.iter_content(1 << 16):
                        chunk = chunk[: want - f.tell()]
                        f.write(chunk)
                        with lock:
                            downloaded += len(chunk)
                        if f.tell() >= want:
                            break
        except PermissionError:
            raise  # retrying cannot fix a refused request
        except Exception as e:  # network drop, timeout, CDN cancel: resume from where we are
            print(f"  retry {part.name}: {type(e).__name__} {e}", flush=True)
            time.sleep(3)


def fetch_file(repo, name, size, sha256, folder=MODELS, revision="main"):
    """repo is "owner/name" for a model, "datasets/owner/name" for a dataset."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if dest.exists() and dest.stat().st_size == size:
        print(f"have {name}", flush=True)
        return
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
    if size == 0:
        dest.write_bytes(b"")
        print(f"done {name} (empty file)", flush=True)
        return
    chunk = -(-size // PARTS)
    ranges = [(s, min(s + chunk, size) - 1) for s in range(0, size, chunk)]  # small files get fewer parts
    parts = [folder / f"{name}.part{i}" for i in range(len(ranges))]
    # Carve an earlier partial download into the parts it already covers.
    if dest.exists() and not any(p.exists() for p in parts):
        with open(dest, "rb") as src:
            for i, part in enumerate(parts):
                src.seek(i * chunk)
                data = src.read(min(chunk, size - i * chunk))
                if not data:
                    break
                part.write_bytes(data)
        dest.unlink()
    with ThreadPoolExecutor(PARTS) as pool:
        futures = [pool.submit(fetch_range, url, part, a, b) for part, (a, b) in zip(parts, ranges)]
        for fut in futures:
            fut.result()  # re-raise a failed range here instead of failing later at the join
    h = hashlib.sha256()
    tmp = folder / f"{name}.joining"
    with open(tmp, "wb") as out:
        for part in parts:
            with open(part, "rb") as f:
                while block := f.read(1 << 20):
                    h.update(block)
                    out.write(block)
    if (sha256 and h.hexdigest() != sha256) or tmp.stat().st_size != size:
        sys.exit(f"CHECKSUM MISMATCH for {name}; parts kept for inspection")
    tmp.rename(dest)
    for part in parts:
        part.unlink()
    print(f"done {name} ({'sha256 ok' if sha256 else 'size ok; no checksum published'})", flush=True)


def report():
    last, t0 = 0, time.time()
    while True:
        time.sleep(30)
        now = downloaded
        print(f"[{time.time() - t0:5.0f}s] {(now - last) / 30 / 1e6:.2f} MB/s, {now / 1e6:.0f} MB this run", flush=True)
        last = now


def lookup(spec, kind="models", revision="main"):
    """'repo/name:file' -> (repo, file, size, sha256, folder) from the Hugging Face API; the file
    goes to models/<name>/. Small non-LFS files have no published SHA-256 and are size-checked only.
    kind="datasets" looks in a dataset repository instead."""
    repo, path = spec.split(":", 1)
    tree = requests.get(f"https://huggingface.co/api/{kind}/{repo}/tree/{revision}?recursive=true", timeout=30).json()
    entry = next(e for e in tree if e["path"] == path)
    return repo, path, entry["size"], (entry.get("lfs") or {}).get("oid"), MODELS / repo.split("/")[1]


if __name__ == "__main__":
    files = [lookup(s) for s in sys.argv[1:]] or FILES
    threading.Thread(target=report, daemon=True).start()
    with ThreadPoolExecutor(FILES_AT_ONCE) as pool:
        for fut in [pool.submit(fetch_file, *f) for f in files]:
            fut.result()
    print("ALL DONE", flush=True)
