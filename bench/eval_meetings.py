#!/usr/bin/env python3
"""Score transcribe.py output on a real meeting against its reference transcript.

The reference is an ElevenLabs transcript with one turn per line, either "[[S#]] text"
(optionally mapped to people with --map) or "Name: text" (names given with --speakers). It has
no timestamps, so everything is measured on the text:

  WER / CER, Arabic-/English-word errors, English kept  - as in bench/score.py
  WER lenient  the same after dropping pure hesitation sounds (آآآ, مم) and unifying common
        Egyptian spelling variants (كده/كدا, ايوه/ايوا, ...) on both sides
  WDER  word diarization error rate: our words and the reference words are aligned
        (edit-distance alignment); for every aligned pair we compare who the word is
        attributed to, after mapping our Speaker 1/2/... to the reference people in the
        way that agrees best. WDER = share of aligned words credited to the wrong person.

With --prefix the hypothesis covers only the start of the recording (an excerpt), and the
reference is cut at the length that aligns best with it.

The reference is itself machine output (ElevenLabs, with speakers corrected by hand), so WER
here means disagreement with ElevenLabs, not absolute error.

Usage:
  .venv/bin/python bench/eval_meetings.py <reference.txt> <transcribe-output.json> [--map map.json]
      [--speakers "Name A,Name B"] [--prefix]
"""
import argparse
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import jiwer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import LATIN_WORD, latin_words, normalize  # noqa: E402

ARTICLES = {"ال", "بال", "وال", "لل", "فال", "كال", "وبال", "ولل"}
# Lenient scoring: pure hesitation sounds dropped and common Egyptian spelling variants unified,
# on both sides. Dialect has no standard spelling (كده/كدا are the same word), and ElevenLabs
# writes hesitations (آآآ) that other systems leave out.
FILLER = re.compile(r"^(ا{2,}|ا?م{2,}|ا+ه{2,}|ه{2,})$")
SPELLING = {"كدا": "كده", "ايوا": "ايوه", "برضه": "برضو", "دا": "ده", "علشان": "عشان", "okay": "ok"}


def lenient(words):
    return [(SPELLING.get(w, w), p) for w, p in words if not FILLER.match(w)]


def script_errors(ref_tokens, hyp_text):
    """Arabic / English reference words substituted or deleted, and how many of each there are. An
    article followed by an English word (normalization splits الـdata into ال data) counts with the
    English word."""
    scripts = ["en" if LATIN_WORD.search(t) or (t in ARTICLES and i + 1 < len(ref_tokens)
                                               and LATIN_WORD.search(ref_tokens[i + 1])) else "ar"
               for i, t in enumerate(ref_tokens)]
    out = jiwer.process_words(" ".join(ref_tokens), hyp_text)
    errors, totals = Counter(), Counter(scripts)
    for c in out.alignments[0]:
        if c.type in ("substitute", "delete"):
            errors.update(scripts[c.ref_start_idx:c.ref_end_idx])
    return errors, totals


def reference_turns(path, names=None, speakers=None):
    """[(person, raw text)] from "[[S#]] text" lines (names maps S# to a person, so several labels
    can be merged) or "Name: text" lines (speakers lists the names)."""
    turns, person = [], None
    for line in Path(path).read_text().splitlines():
        m = re.match(r"\s*\[\[(S\d+)\]\]\s*(.*)", line)
        if m:
            person, line = (names or {}).get(m.group(1), m.group(1)), m.group(2)
        else:
            for name in speakers or ():
                if line.startswith(name + ":"):
                    person, line = name, line[len(name) + 1:]
                    break
        if line.strip():
            turns.append((person, line))
    return turns


def words_of(turns):
    return [(w, p) for p, text in turns for w in normalize(text).split()]


def hypothesis_words(lines):
    """[(word, speaker)] from transcribe.py lines ({"speaker", "text"})."""
    return [(w, f"spk{x['speaker']}") for x in lines for w in normalize(x["text"]).split()]


def best_prefix(ref_words, hyp_words):
    """Number of reference words that aligns best with a hypothesis covering only the start."""
    hyp = " ".join(w for w, _ in hyp_words)

    def wer_at(k):
        return jiwer.wer(" ".join(w for w, _ in ref_words[:k]), hyp) if k else 1.0

    lo, hi = max(1, len(hyp_words) // 2), min(len(ref_words), 2 * len(hyp_words))
    step = max(1, (hi - lo) // 40)
    k = min(range(lo, hi + 1, step), key=wer_at)
    return min(range(max(1, k - step), min(len(ref_words), k + step) + 1), key=wer_at)


def best_mapping(pairs):
    """Map hypothesis speakers to reference people to maximize agreeing aligned words."""
    counts = defaultdict(Counter)
    for hyp, ref in pairs:
        counts[hyp][ref] += 1
    hyps, refs = sorted(counts), sorted({r for _, r in pairs})
    if len(hyps) <= 8 and len(refs) <= 10:  # exact search; each hyp speaker to a distinct person
        best, best_score = {}, -1
        for perm in itertools.permutations(refs + [None] * max(0, len(hyps) - len(refs)), len(hyps)):
            score = sum(counts[h][r] for h, r in zip(hyps, perm) if r is not None)
            if score > best_score:
                best, best_score = dict(zip(hyps, perm)), score
        return best
    return {h: counts[h].most_common(1)[0][0] for h in hyps}  # greedy fallback


def evaluate(ref_turns, hyp, prefix=False):
    """ref_turns: [(person, raw text)]; hyp: [(word, speaker)] (already normalized words)."""
    ref = words_of(ref_turns)
    if prefix:
        ref = ref[:best_prefix(ref, hyp)]
    ref_text, hyp_text = " ".join(w for w, _ in ref), " ".join(w for w, _ in hyp)
    out = jiwer.process_words(ref_text, hyp_text)
    pairs, aligned = [], []
    for c in out.alignments[0]:
        if c.type in ("equal", "substitute"):
            for i, j in zip(range(c.ref_start_idx, c.ref_end_idx), range(c.hyp_start_idx, c.hyp_end_idx)):
                pairs.append((hyp[j][1], ref[i][1]))
                aligned.append(j)
    mapping = best_mapping(pairs)
    agree = sum(1 for h, r in pairs if mapping.get(h) == r)
    errors, totals = script_errors([w for w, _ in ref], hyp_text)
    en_ref, en_hit = latin_words(ref_text.split()), latin_words(ref_text.split()) & latin_words(hyp_text.split())
    ref_people = Counter(p for _, p in ref)
    lref, lhyp = lenient(ref), lenient(hyp)
    lout = jiwer.process_words(" ".join(w for w, _ in lref), " ".join(w for w, _ in lhyp))
    cout = jiwer.process_characters(ref_text, hyp_text)
    # [errors (or hits), out of] for every rate, so several excerpts can be pooled exactly
    counts = {
        "wer": [out.substitutions + out.deletions + out.insertions, len(ref)],
        "wer_lenient": [lout.substitutions + lout.deletions + lout.insertions, len(lref)],
        "cer": [cout.substitutions + cout.deletions + cout.insertions, cout.substitutions + cout.deletions + cout.hits],
        "ar_err": [errors["ar"], totals["ar"]],
        "en_err": [errors["en"], totals["en"]],
        "en_kept": [sum(en_hit.values()), sum(en_ref.values())],
        "wder": [len(pairs) - agree, len(pairs)],
    }
    return {
        "ref_words": len(ref), "hyp_words": len(hyp),
        **{k: n / max(1, d) for k, (n, d) in counts.items()},
        "counts": counts, "aligned_words": len(pairs),
        "ref_speakers": len(ref_people), "ref_speakers_20w": sum(1 for n in ref_people.values() if n >= 20),
        "hyp_speakers": len({s for _, s in hyp}),
        "mapping": mapping,
        # for every aligned hypothesis word (its index in hyp): credited to the right person or not
        "right_speaker": {j: mapping.get(h) == r for j, (h, r) in zip(aligned, pairs)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("hypothesis", help="transcribe.py .json output")
    ap.add_argument("--map", help="JSON file or string mapping S# labels to people")
    ap.add_argument("--speakers", help="comma-separated names for 'Name: text' references")
    ap.add_argument("--prefix", action="store_true", help="the hypothesis covers only the start of the reference")
    args = ap.parse_args()
    names = None
    if args.map:
        names = json.loads(Path(args.map).read_text() if Path(args.map).exists() else args.map)
    turns = reference_turns(args.reference, names, args.speakers.split(",") if args.speakers else None)
    hyp = hypothesis_words(json.loads(Path(args.hypothesis).read_text()))
    r = evaluate(turns, hyp, args.prefix)
    del r["right_speaker"]
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
