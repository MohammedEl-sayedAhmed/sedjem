#!/usr/bin/env python3
"""Score benchmark results, print a summary and optionally write the docs tables.

For each results/<set>.<audio>.<model>.<config>.jsonl whose set has reference
transcripts, reports:
  WER / CER    corpus-level word and character error rates after normalization, with a
               95% bootstrap confidence interval for WER (clips resampled 2000 times)
  AR err / EN err
               the WER split by the script of the reference word: substitutions and
               deletions of Arabic words / English words, over the count of each
               (insertions are left out). An Arabic prefix written onto an English word
               (الـdiscount, ال+usual) counts with the English word, so writing the term in
               Arabic letters is an English error, not an Arabic one.
  EN kept      share of the reference's English words that appear in the hypothesis in
               Latin script (transliterating or translating an English term is a miss)
  Latin share  share of hypothesis words written in Latin script
  RTF          processing time / speech duration on this machine (the 0.5 s of silence
               added to every clip is not counted; lower is faster)

Normalization (applied to both sides): remove ArzEn's event tags ([HES], [LAUGHTER],
[HUM], [NOISE]), brackets and the ArzEn joiners + and *; strip Arabic diacritics and
tatweel; map alef variants to ا, ى to ي, ة to ه; Arabic-Indic digits to 0-9; remove
punctuation; put a space between Arabic and Latin letters (ال+usual, ال[deep inside]);
lowercase Latin.

Usage:
  .venv/bin/python bench/score.py [--tables docs/results-tables.md]
  .venv/bin/python bench/score.py --compare results/A.jsonl results/B.jsonl   # paired WER difference
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import jiwer
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
PUBLIC_SETS = ("perle", "arzen", "mixat")  # results of any other set are private and never scored here
TAIL_S = 0.5  # silence appended to every prepared clip (bench/prepare_data.py)

EVENT_TAGS = re.compile(r"\[(?:HES|LAUGHTER|HUM|NOISE)\]")  # ArzEn's event tags, by name only
DIACRITICS = re.compile(r"[ً-ْٰـ]")
LATIN_WORD = re.compile(r"[a-z]")
GLUE = "\x01"  # marks an Arabic prefix written onto an English word
CHAR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه",
                          **{chr(0x660 + i): str(i) for i in range(10)},
                          **{chr(0x6F0 + i): str(i) for i in range(10)}})

MODEL_NAMES = {
    "cohere-transcribe-arabic-07-2026-Q4_K_M": "Cohere Transcribe Arabic (Q4)",
    "whisper-whisper-medium-arabic-codeswitched-ct2": "whisper-medium code-switching fine-tune",
    "whisper-large-v3": "Whisper large-v3",
    "Audar-ASR-V1-Turbo-Q4_K_M": "Audar-ASR-V1-Turbo (Q4)",
    "Confucius4-R2T2-Q8_0": "R2T2 (Q8)",
    "Qwen3-ASR-1.7B-Q8_0": "Qwen3-ASR-1.7B (Q8)",
}
SET_NAMES = {"perle": "Perle", "arzen": "ArzEn"}
AUDIO_NAMES = {"wav16k": "16 kHz", "phone": "through 8 kHz", "g711": "telephone codec (G.711)"}
AUDIO_HEADINGS = {"wav16k": "at 16 kHz", "phone": "through 8 kHz", "g711": "through a telephone codec (G.711)"}
ALSO_TRIED = ("Confucius4-R2T2-Q8_0", "Qwen3-ASR-1.7B-Q8_0", "Audar-ASR-V1-Turbo-Q4_K_M")  # last, in their own table


def normalize(text, mark_glued=False):
    text = EVENT_TAGS.sub(" ", text)
    if mark_glued:  # الـdiscount, ال+usual, الdata: remember the prefix belongs to the English word
        text = re.sub(r"(?<=[ء-ي])(?:ـ+\s?|[+*])?(?=[A-Za-z])", GLUE, text)
    text = re.sub(r"[\[\]+*#]", " ", text)
    text = DIACRITICS.sub("", text).translate(CHAR_MAP)
    text = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in text)
    text = re.sub(r"(?<=[؀-ۿ])(?=[A-Za-z])|(?<=[A-Za-z])(?=[؀-ۿ])", " ", text)
    return " ".join(text.replace(GLUE, GLUE + " ").lower().split())


def reference_tokens(raw):
    """Normalized reference tokens and the script each counts as ("ar"/"en")."""
    tokens = normalize(raw, mark_glued=True).split()
    return ([t.replace(GLUE, "") for t in tokens],
            ["en" if GLUE in t or LATIN_WORD.search(t) else "ar" for t in tokens])


def latin_words(words):
    return Counter(w for w in words if LATIN_WORD.search(w))


def errors_by_script(raw_refs, hyps):
    """Share of Arabic / English reference words substituted or deleted. raw_refs are
    unnormalized reference strings, hyps normalized hypothesis strings."""
    tokens, scripts = zip(*(reference_tokens(r) for r in raw_refs))
    out = jiwer.process_words([" ".join(t) for t in tokens], list(hyps))
    errors, totals = Counter(), Counter(s for ss in scripts for s in ss)
    for ss, chunks in zip(scripts, out.alignments):
        for c in chunks:
            if c.type in ("substitute", "delete"):
                errors.update(ss[c.ref_start_idx:c.ref_end_idx])
    return {k: errors[k] / max(1, totals[k]) for k in ("ar", "en")}


def clip_errors(ref, hyp):
    """Per-clip [edit errors, reference words] for bootstrapping corpus WER."""
    counts = []
    for r, h in zip(ref, hyp):
        if not r.split():  # nothing to compare against: every hypothesis word is an insertion
            counts.append([len(h.split()), 0])
            continue
        o = jiwer.process_words(r, h)
        counts.append([o.substitutions + o.deletions + o.insertions, len(r.split())])
    return np.array(counts)


def bootstrap(counts, n=2000, seed=0):
    idx = np.random.default_rng(seed).integers(0, len(counts), (n, len(counts)))
    sums = counts[idx].sum(1)
    return np.percentile(sums[:, 0] / sums[:, 1], [2.5, 97.5])


def load(path, refs):
    rows = [json.loads(line) for line in open(path) if line.strip()]
    rows = [r for r in rows if r["id"] in refs]
    return rows, [normalize(refs[r["id"]]) for r in rows], [normalize(r["hyp"]) for r in rows]


def score(path, refs):
    rows, ref, hyp = load(path, refs)
    en_ref = sum((latin_words(x.split()) for x in ref), Counter())
    en_hit = sum((latin_words(r.split()) & latin_words(h.split()) for r, h in zip(ref, hyp)), Counter())
    hyp_words = [w for h in hyp for w in h.split()]
    split = errors_by_script([refs[r["id"]] for r in rows], hyp)
    lo, hi = bootstrap(clip_errors(ref, hyp))
    return {
        "clips": len(rows),
        "wer": jiwer.wer(ref, hyp), "wer_ci": [lo, hi],
        "cer": jiwer.cer(ref, hyp),
        "ar_err": split["ar"],
        "en_err": split["en"],
        "en_kept": sum(en_hit.values()) / max(1, sum(en_ref.values())),
        "latin_share": sum(latin_words(hyp_words).values()) / max(1, len(hyp_words)),
        "rtf": sum(r["seconds"] for r in rows) / sum(r["audio_s"] - TAIL_S for r in rows),
    }


def references(test_set):
    manifest = ROOT / "data" / test_set / "manifest.jsonl"
    if not manifest.exists():
        return None
    return {x["id"]: x["reference"] for x in map(json.loads, open(manifest)) if "reference" in x}


def parse_name(path):
    test_set, audio, rest = path.stem.split(".", 2)
    model, config = rest.rsplit(".", 1)  # model names can contain dots (Qwen3-ASR-1.7B)
    return test_set, audio, model, config


def config_label(path, config):
    """Readable setting, e.g. "Arabic forced + hint"; from the .meta.json sidecar when it has one."""
    meta = path.with_suffix(".meta.json")
    label = json.loads(meta.read_text()).get("label") if meta.exists() else None
    base = {"ar": "Arabic forced", "auto": "auto-detect"}.get(config.split("-")[0], config)
    gpu = ", GPU" if "gpu" in config.split("-") else ""
    return (label or (base + (" + hint" if "-p" in config else ""))) + gpu


def current_rows(summary):
    """Drop whole-clip results where the same model and setting also has a pipeline result."""
    have = {(s["set"], s["audio"], s["model"], s["label"]) for s in summary if not s["config"].endswith("-clip")}
    return [s for s in summary if not (s["config"].endswith("-clip")
                                       and (s["set"], s["audio"], s["model"], s["label"]) in have)]


def compare(a, b):
    """Paired bootstrap of WER(a) - WER(b) on the clips both files share."""
    refs = references(parse_name(a)[0])
    ra, ref_a, hyp_a = load(a, refs)
    rb = {r["id"]: normalize(r["hyp"]) for r in load(b, refs)[0]}
    keep = [i for i, r in enumerate(ra) if r["id"] in rb]
    ref = [ref_a[i] for i in keep]
    ca, cb = clip_errors(ref, [hyp_a[i] for i in keep]), clip_errors(ref, [rb[ra[i]["id"]] for i in keep])
    idx = np.random.default_rng(0).integers(0, len(keep), (2000, len(keep)))
    diff = ca[idx, 0].sum(1) / ca[idx, 1].sum(1) - cb[idx, 0].sum(1) / cb[idx, 1].sum(1)
    lo, hi = np.percentile(diff, [2.5, 97.5])
    print(f"{a.name} - {b.name}: {ca[:, 0].sum() / ca[:, 1].sum() - cb[:, 0].sum() / cb[:, 1].sum():+.1%} "
          f"(95% CI {lo:+.1%} to {hi:+.1%}, {len(keep)} clips, {int(ca[:, 1].sum())} reference words)")


def write_tables(summary, path):
    """The docs' result tables: Perle, the public set closest to the meetings, then ArzEn, each
    condition sorted by WER; the models that were also tried come last, in a table of their own."""
    out = ["# Result tables (generated by `bench/score.py --tables`)", "",
           "WER with a 95% bootstrap confidence interval over clips. Speed = processing time / speech "
           "time on the test laptop (0.35 = about 3x faster than real time). Rows marked *whole clip* "
           "come from the first round, when the whole clip went to the model and Whisper decoded without "
           "word timestamps; the other rows use the same path as `transcribe.py` (VAD chunks, word "
           "timestamps, gap-fill). Perle, Egyptian tech and work talk, is the public set closest to the "
           "meetings. The leading models were probably trained on ArzEn, so its numbers flatter them (see "
           "the caveats in [the results](03-results.md#caveats)). R2T2, Qwen3-ASR and Audar, which were "
           "also tried, are in the last table.", ""]
    columns = ("| WER (95% CI) | CER | Arabic-word errors | English-word errors | English kept | Speed |",
               "|---|---|---|---|---|---|")

    def cells(s):
        lo, hi = s["wer_ci"]
        label = s["label"] + (" *(whole clip)*" if s["config"].endswith("-clip") else "")
        return (f"| {MODEL_NAMES.get(s['model'], s['model'])} | {label} | {s['wer']:.1%} ({lo:.0%}–{hi:.0%}) "
                f"| {s['cer']:.1%} | {s['ar_err']:.1%} | {s['en_err']:.0%} | {s['en_kept']:.0%} | {s['rtf']:.2f} |")

    summary = sorted(current_rows(summary), key=lambda s: (s["wer"], s["model"], s["config"]))
    conditions = [(t, a) for t in ("perle", "arzen") for a in ("wav16k", "phone", "g711")]
    for test_set, audio in conditions:
        rows = [s for s in summary if (s["set"], s["audio"]) == (test_set, audio) and s["model"] not in ALSO_TRIED]
        if rows:
            out += [f"## {SET_NAMES.get(test_set, test_set)} {AUDIO_HEADINGS.get(audio, audio)}", "",
                    "| Model | Setting " + columns[0], "|---|---" + columns[1], *map(cells, rows), ""]
    also = [s for c in conditions for s in summary if (s["set"], s["audio"]) == c and s["model"] in ALSO_TRIED]
    if also:
        out += ["## Also tried: R2T2, Qwen3-ASR and Audar", "",
                "| Test set | Audio | Model | Setting " + columns[0], "|---|---|---|---" + columns[1],
                *(f"| {SET_NAMES.get(s['set'], s['set'])} | {AUDIO_NAMES.get(s['audio'], s['audio'])} {cells(s)}"
                  for s in also), ""]
    Path(path).write_text("\n".join(out), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", help="also write markdown tables to this file")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("A", "B"), help="paired WER difference of two result files")
    args = ap.parse_args()
    if args.compare:
        compare(*args.compare)
        return
    summary, cache = [], {}
    for path in sorted(RESULTS.glob("*.jsonl")):
        if path.stat().st_size == 0:
            continue
        test_set, audio, model, config = parse_name(path)
        if test_set not in PUBLIC_SETS:
            print(f"skipping {path.name}: not a public test set", file=sys.stderr)
            continue
        if test_set not in cache:
            cache[test_set] = references(test_set)
            if cache[test_set] is None:
                print(f"skipping {test_set}: data/{test_set}/manifest.jsonl not found "
                      f"(run bench/prepare_data.py {test_set})", file=sys.stderr)
        refs = cache[test_set]
        if not refs:
            continue
        summary.append({"set": test_set, "audio": audio, "model": model, "config": config,
                        "label": config_label(path, config), **score(path, refs)})
    print("| set | audio | model | config | clips | WER (95% CI) | CER | AR err | EN err | EN kept | Latin share | RTF |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in summary:
        lo, hi = s["wer_ci"]
        print(f"| {s['set']} | {s['audio']} | {s['model']} | {s['config']} | {s['clips']} "
              f"| {s['wer']:.1%} ({lo:.0%}–{hi:.0%}) | {s['cer']:.1%} | {s['ar_err']:.1%} | {s['en_err']:.0%} "
              f"| {s['en_kept']:.0%} | {s['latin_share']:.0%} | {s['rtf']:.2f} |")
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1))
    if args.tables:
        write_tables(summary, args.tables)


if __name__ == "__main__":
    main()
