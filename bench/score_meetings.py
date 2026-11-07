#!/usr/bin/env python3
"""Score every engine's transcribe.py output on the private meeting excerpts.

Reads data/private-meetings/manifest.jsonl and results/meetings/<engine>/<id>.*.json (both
git-ignored; every subfolder of results/meetings/ is scored as one engine, named after the
folder), scores each against its reference with bench/eval_meetings.py (the reference is cut to
the excerpt by alignment) and prints per-excerpt and pooled numbers with no meeting content:
WER/CER against ElevenLabs, Arabic-/English-word errors, English kept, WDER, and speed and peak
memory from the .meta.json that transcribe.py writes. Pooled rates are totals over all excerpts
(e.g. English kept = English words kept in all excerpts / English words in all excerpts).

Usage: .venv/bin/python bench/score_meetings.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
from eval_meetings import best_mapping, evaluate, hypothesis_words, reference_turns, words_of  # noqa: E402


def main():
    items = [json.loads(line) for line in open(ROOT / "data" / "private-meetings" / "manifest.jsonl")]
    if not (ROOT / "results" / "meetings").is_dir():
        sys.exit("nothing to score: run transcribe.py on data/private-meetings/*.wav with "
                 "--out results/meetings/<engine> first (see README)")
    rows = []
    for engine_dir in sorted((ROOT / "results" / "meetings").iterdir()):
        if not engine_dir.is_dir() or engine_dir.name == "bench":
            continue
        for item in items:
            found = sorted(p for p in engine_dir.glob(f"{item['id']}.*.json")
                           if not p.name.endswith((".words.json", ".meta.json")))
            meta_path = found[0].with_name(found[0].name[:-5] + ".meta.json") if found else None
            if not found or not meta_path.exists():  # not run yet, or still running (partial output)
                continue
            meta = json.loads(meta_path.read_text())
            turns = reference_turns(item["reference"], speakers=item["speakers"])
            r = evaluate(turns, hypothesis_words(json.loads(found[0].read_text())), prefix=True)
            rows.append({"engine": engine_dir.name, "meeting": item["id"], "speakers": item["n_speakers"],
                         **{k: r[k] for k in ("ref_words", "wer", "wer_lenient", "cer", "ar_err", "en_err", "en_kept",
                                              "wder", "hyp_speakers", "counts")},
                         "rtf": meta["rtf"], "peak_rss_mb": meta["peak_rss_mb"]})
    # Baseline: ElevenLabs' own speaker tags (before the hand corrections) on the same stretch.
    # The words are identical in both files, so they align one to one.
    for item in items:
        done = [r for r in rows if r["meeting"] == item["id"]]
        raw_path = Path(item["reference"].replace(".named.txt", ".elevenlabs.txt"))
        if not done or not raw_path.exists():
            continue
        k = max(r["ref_words"] for r in done)
        named = words_of(reference_turns(item["reference"], speakers=item["speakers"]))[:k]
        raw = words_of(reference_turns(raw_path))[:k]
        if [w for w, _ in named] != [w for w, _ in raw]:
            print(f"{item['id']}: ElevenLabs file words differ from the reference; baseline skipped", file=sys.stderr)
            continue
        pairs = [(s, p) for (_, s), (_, p) in zip(raw, named)]
        mapping = best_mapping(pairs)
        wrong = sum(mapping.get(s) != p for s, p in pairs)
        rows.append({"engine": "elevenlabs-raw-labels", "meeting": item["id"], "speakers": item["n_speakers"],
                     "ref_words": k, "wder": wrong / len(pairs), "counts": {"wder": [wrong, len(pairs)]}})
    (ROOT / "results" / "meetings" / "summary.json").write_text(json.dumps(rows, indent=1))
    print("| engine | meeting | speakers | ref words | WER vs ElevenLabs | lenient | CER | Arabic-word err "
          "| English-word err | English kept | WDER | speed | peak RAM |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "wer" not in r:  # the ElevenLabs label baseline has speaker labels only
            print(f"| {r['engine']} | {r['meeting']} | {r['speakers']} | {r['ref_words']} | | | | | | | {r['wder']:.1%} | | |")
            continue
        print(f"| {r['engine']} | {r['meeting']} | {r['speakers']} | {r['ref_words']} | {r['wer']:.1%} "
              f"| {r['wer_lenient']:.1%} | {r['cer']:.1%} | {r['ar_err']:.1%} | {r['en_err']:.0%} | {r['en_kept']:.0%} "
              f"| {r['wder']:.1%} | {r['rtf']:.2f} | {r['peak_rss_mb'] / 1024:.1f} GB |")
    print("\nPooled over excerpts (errors summed over all excerpts, divided by the summed word counts):")
    for engine in sorted({r["engine"] for r in rows}):
        rs = [r for r in rows if r["engine"] == engine]
        w = sum(r["ref_words"] for r in rs)
        avg = {k: sum(r["counts"][k][0] for r in rs) / max(1, sum(r["counts"][k][1] for r in rs))
               for k in rs[0]["counts"]}
        if "wer" not in avg:
            print(f"  {engine}: {len(rs)} excerpts, {w} words | WDER {avg['wder']:.1%}")
            continue
        print(f"  {engine}: {len(rs)} excerpts, {w} words | WER {avg['wer']:.1%} (lenient {avg['wer_lenient']:.1%}) "
              f"| CER {avg['cer']:.1%} | Arabic-word {avg['ar_err']:.1%} | English-word {avg['en_err']:.0%} "
              f"| English kept {avg['en_kept']:.0%} | WDER {avg['wder']:.1%} "
              f"| speed {sum(r['rtf'] for r in rs) / len(rs):.2f}x real time")


if __name__ == "__main__":
    main()
