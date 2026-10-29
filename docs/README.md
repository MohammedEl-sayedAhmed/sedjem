# Project notes

These notes describe how Sedjem came about: what was tested, what broke, what the numbers say and
what to use for which job. The work was done on 24 and 25 September 2026 on one test laptop, a
Lenovo ThinkPad L14 Gen 3 with a 12th Gen Intel Core i5-1245U (10 cores, 12 threads), 16 GB of RAM
and Intel Iris Xe integrated graphics, no NVIDIA GPU, running Kubuntu 24.04; speeds on other
computers will differ.

## Summary

The use case is transcripts of recorded meetings and calls in Egyptian Arabic mixed with English
(code-switching: Arabic sentences full of English tech terms and whole English phrases), made on
your own computer so that the audio stays private, with each speaker labelled by voice (Speaker 1,
Speaker 2 and so on), and free to try.

Two open models handle it. Cohere Transcribe Arabic had the lowest error rate on the public
Egyptian set (13.4%), made by far the fewest errors on Arabic words, and processed meetings in
about a quarter of their length. A Whisper medium fine-tune for Arabic–English code-switching scored
18.5%, kept about 90% of English terms in English and ran at about half real time. The gap between
their overall error rates is not statistically proven with this test set; the differences on Arabic
words and on English terms are clear. Stock Whisper large-v3 with a one-sentence Egyptian style
hint came close (20.8%) but is the slowest of the three.

On six real work meetings both models were far behind ElevenLabs. Their text differed from its
transcripts on 44–49% of the words, mostly through English terms that were dropped or written in
Arabic letters and phrases lost in fast exchanges. For minutes that people rely on, ElevenLabs is
the better choice; the local models suit private or offline drafts.

Speaker labels by voice worked well locally. With the TitaNet-small voiceprint model, 11–12% of
words went to the wrong person across the six meetings, and 8–9% across the five with two or three
people (2–11% on four of them, 17% on one where ElevenLabs' own labels were 27% off). ElevenLabs'
own labels were about 6% off overall. The number of speakers was estimated correctly in five of
the six meetings. NVIDIA's Nemotron-3-Diarization, which can be chosen instead, put the least on the
wrong person on these meetings and on two public sets, ArzEn's interviews and AMI's English
meetings, most clearly in meetings of four or more people. It is ten times the size and four to six
times slower, so it is the choice for larger meetings, and TitaNet-small stays the default.

Also tried, and not usable for this: R2T2, the model the project started from, tried first (47.1%
on the public Egyptian set with the whole clip, and most English terms written in Arabic letters),
its base model Qwen3-ASR, and Audar-ASR.

All of this runs offline from the command line:

```sh
.venv/bin/python transcribe.py path/to/call.wav --speakers 2      # or --speakers 0 to estimate
```

Sedjem wraps the same pipeline in an app (`./app.sh`) that runs any of the five models, three
local and two hosted with your own key, shows progress, and lets you play back, search, correct and
export the transcript ([the app](09-app.md)). It also runs as a desktop app on Windows and Linux,
with the local models downloaded from inside the app ([desktop app](10-desktop.md)).

## Main results

| | Public Egyptian set (Perle, 16 kHz) | Real meetings, share of words that differ from ElevenLabs | ArzEn conversations | English kept (Perle / meetings) | Speed, × real time (meetings / Perle clips) |
|---|---|---|---|---|---|
| Cohere Transcribe Arabic | 13.4% | 43.8% (with speaker labels) | 6.7% | 77% / 46% | 0.26 / 0.36 |
| whisper-medium code-switching (default) | 18.5% | 48.8% | 10.8% | 90% / 57% | 0.53 / 1.08 |
| Whisper large-v3 + style hint | 20.8% | not run | 35.7% | 88% / not run | not run / 2.15 |
| R2T2, also tried | 47.1% (whole clip) | not run | 43.9% (whole clip) | 25% / not run | not run / 0.82 (whole clip) |

The Perle and ArzEn columns are word error rates (lower is better). The two leading models were
almost certainly trained on ArzEn, so its column flatters them. The meeting column compares against
the ElevenLabs transcript, which is machine output itself. Speed is processing time divided by audio
length. The meeting figures come from 10-minute excerpts run through `transcribe.py` with speaker
labels, pauses included; the Perle clips are 4 to 12 seconds long, so the fixed cost per chunk
weighs more there.

| Speaker labels | Meeting excerpts, words on the wrong person | ArzEn interviews, speech on the wrong person | AMI meetings, speech on the wrong person | Count estimated right (excerpts / ArzEn / AMI) | Seconds per 10 minutes | Size |
|---|---|---|---|---|---|---|
| TitaNet-small voiceprints (default) | 10.2% | 2.5% | 5.3% | 5 of 6 / 38 of 38 / 15 of 16 | 5–8 | 40 MB |
| Nemotron-3-Diarization | 10.0% | 2.2% | 2.3% | 6 of 6 / 29 of 38 / 14 of 16 | 29–34 | 397 MB |

These are the shares given to the wrong person (lower is better) with the number of people
estimated, as the app does by default. ArzEn (Egyptian Arabic–English interviews) and AMI (English
meetings, with much overlap) are public sets, scored on 10 ms frames. The time is for the speaker
step alone. The other two speaker models, TitaNet-large and 3D-Speaker CAM++, the error rates by
the usual measure (DER) and the charts are in [speaker labels](04-speaker-labels.md#conclusions).

## Contents

| Document | What's in it |
|---|---|
| [Use case](01-use-case.md) | what the transcripts are for, the test audio, the original question |
| [Trials and issues](02-trials-and-issues.md) | every experiment in order (38 of them, in two rounds): what broke and how it was fixed |
| [Results](03-results.md) | method, the Perle benchmark, phone audio, real meetings, ArzEn, the test call, long recordings, caveats |
| [Speaker labels](04-speaker-labels.md) | how voices are told apart, the bugs found, the speaker models compared on the meetings, ArzEn and AMI, conclusions and a recommendation, forced alignment, references |
| [Command line](05-command-line.md) | setup, options, what happens inside, speed and memory |
| [Market research](06-market-research.md) | hosted and open options, free tiers, privacy terms ([full report](research/market-research-report.md), [second pass on hosted APIs](research/hosted-apis-report.md)) |
| [Recommendation](07-recommendation.md) | what to use when, and what to try next |
| [Disk space and cleanup](08-disk-and-cleanup.md) | what the project puts on disk and how to remove it |
| [The app](09-app.md) | using Sedjem, the model settings in `app/config.toml`, API keys, privacy |
| [Desktop app](10-desktop.md) | Sedjem on Windows and Linux: running, building, the installer, what has been verified |
| [Assistants](11-assistants.md) | connecting Claude, Cursor or VS Code over MCP, the tools, resources and prompts, what assistants may see, the log |
| [Updates](12-updates.md) | the two channels, the first-start question and what is sent to GitHub, *Update now* for each kind of install, updating by hand, nightly previews |
| [Name and logo](brand/README.md) | what Sedjem means, the idea behind the logo, its versions and files, and how to use them |
| [Result tables](results-tables.md) | every configuration on every public test set, generated by `bench/score.py --tables` |

The [research](research/) folder keeps the raw material: the two market-research reports, their
source data, and the handoff note the project started from.

The charts in these notes are drawn by `docs/charts.py` from `results/summary.json`, from the
speaker-label scores in `results/arzen-diarization.json` and `results/ami-diarization.json` and, for
the private meetings, from the published numbers copied into `docs/charts-data.json`. After the
results change, redraw them with `python docs/charts.py`; `tests/test_charts.py` checks that every
number drawn matches its table.
