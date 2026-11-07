# Trials and issues

This log starts from the project's first question, whether R2T2 could replace ElevenLabs Scribe; the
conclusions for the real use case, Egyptian Arabic–English meetings and calls, are in
[the results](03-results.md) and [the recommendation](07-recommendation.md).

Every experiment in the order it was run, on 24 and 25 September 2026, with what went wrong and what
was done about it. Numbers are word error rates (WER, lower is better) unless stated otherwise. Full
tables are in [the results](03-results.md), and [the method](03-results.md#method) explains how they
were measured.

## Setup

1. Review of the handoff note. Its verdict rested mainly on one argument, that R2T2 had no Arabic
   in its training data, and the claim that it needs a GPU had not been checked. The plan was to run
   it on a CPU with llama.cpp and measure.
2. The test laptop. The disk was full (1.5 GB free), there was no NVIDIA GPU, and 4–8 GB of RAM was free.
   Clearing a package-manager download cache brought the disk to 18 GB free.
3. Model downloads (R2T2 and Qwen3-ASR, 4.7 GB). The network gave only about 1.2–1.7 MB/s, `curl`
   failed with an HTTP/2 stream CANCEL error, and Hugging Face's `hf_xet` downloader stalled at 0
   bytes. `bench/fetch_models.py` was written to download byte ranges in parallel over HTTP/1.1,
   resume after drops and check SHA-256. All files verified.
4. Test data. The first benchmark used Mixat, a public code-switched set, until the first
   transcript showed that the test call was Egyptian while Mixat is Emirati. The main benchmark moved
   to ArzEn (Egyptian Arabic–English), and later to Perle (Egyptian sentences about tech and work),
   which the market research found.

## First runs on the test call

5. Whisper large-v3 on the 2-minute call, the first end-to-end run. It worked, but took 6.4 times
   the length of the audio (in power-saver mode, see 7), and wrote about half the English terms in
   Latin letters and half in Arabic letters. Kept as the baseline.
6. R2T2 on a CPU with upstream llama.cpp, which the handoff had not verified. The launcher failed
   because the shell's startup file broke under `set -u`; the launchers were rewritten in plain POSIX
   `sh`. R2T2 does run on a CPU.
7. Speed tuning. Generation stayed at about 4.5 tokens/s whatever the thread count, because the
   test laptop was in power-saver mode with the CPU at 0.4–1.2 GHz. Eight threads worked best, and the
   performance power profile was used for test runs from then on (and switched back afterwards).
8. R2T2 on the call: 1.8 times real time (measured before that switch, in power-saver mode), with
   clearly more errors than Whisper: misheard tech terms and dropped clauses.
9. The base model, Qwen3-ASR, on the call: 1.4 times real time in power-saver mode, slightly more
   complete than R2T2, with the same kinds of errors.

## Speaker labels

10. sherpa-onnx's built-in diarization (pyannote segmentation, WeSpeaker voiceprints, its own
    clustering). Left to count the speakers, it found 4, two of them with under 5 s each. Told there
    were 2, it gave Speaker 1 111 s and Speaker 2 14 s, lumping both voices together, and Whisper
    invented a short phrase for the tiny fragments left to Speaker 2. Rejected.
11. Feedback on that attempt: speakers had to be separated by the sound of their voices. The
    speaker step was rebuilt as `speakers.py`: a WeSpeaker voiceprint every 0.75 s, grouped by
    spectral clustering (NME-SC), and each word given the voice active at its timestamp. The result
    was 57 s and 44 s, with turns alternating as in a real conversation. Details in
    [speaker labels](04-speaker-labels.md).
12. Word timestamps, needed to label each word. Whisper's timestamp decoding mode dropped some
    English words. Decoding without timestamp tokens and aligning the words afterwards brought them
    back.
13. Whisper then invented `شكراً لكم` ("thank you all") at the end of the call: with word
    timestamps, faster-whisper decodes the trailing silence again. Since chunks are under 30 s,
    stopping after the first decoding window fixed it.

## Accuracy benchmark

14. ArzEn, 40 clips, with R2T2, Qwen3-ASR (Arabic forced, or language detected) and Whisper. Whisper
    scored worse (47.4%) than Qwen3-ASR (43.3%) and R2T2 (43.9%). Looking at its errors: Whisper
    sometimes rewrote Egyptian into formal Arabic (`ولكن بعد ذلك عندما دخلت…`), dropped filler words
    and spelled English in Arabic letters.
15. A style hint: one Egyptian sentence with English terms in Latin letters, given to Whisper as its
    `initial_prompt`. Whisper went from 47.4% to 33.7%, and the share of English words kept in
    English from 12% to 54%. The same hint as "context" for Qwen3-ASR and R2T2 did not help (43.3 to
    43.9 and 43.9 to 46.5).
16. The style hint on the test call. Whisper stopped early in one chunk and 12 s of speech went
    missing. Gap-fill was added: if the words end more than 2 s before the chunk's speech does, the
    rest is transcribed separately. The transcript was then complete.
17. Phone quality, with every ArzEn clip resampled through 8 kHz. Whisper with the hint went from
    33.7% to 38.0% and Qwen3-ASR from 43.3% to 45.8%, so 8 kHz cost about 2.5–4.3 points.
18. Market research: 142 candidate models and services from five independent web searches, with the
    important claims re-checked against official pages by two separate verification passes. Top
    picks: ElevenLabs Scribe and Speechmatics (hosted), Audar-ASR and Cohere Transcribe Arabic
    (local). See [market research](06-market-research.md).
19. Perle (Egyptian tech and work), 40 clips: Whisper with the hint 22.4%, Whisper 39.7%, R2T2
    47.1%, Qwen3-ASR 50.6%.
20. A new candidate, `whisper-medium-arabic-codeswitched` (0.8 GB). Its ArzEn scores were
    suspiciously low, as if it had been trained on ArzEn, so it was judged on Perle and the test call
    only: 17.6% on Perle with 91% of English kept, and faster than large-v3.
21. Audar-ASR-V1-Turbo, first on the open leaderboard, on Perle. Good Arabic, but it kept only 12%
    of English words in Latin script; almost all the rest came out in Arabic letters, the same habit
    as its Qwen3-ASR base. 43.5%, not pursued further.
22. Cohere Transcribe Arabic, second on the leaderboard, through transcribe.cpp: 15.7% on Perle
    (whole clip), at about three times faster than real time.
23. A contamination check. On ArzEn, Cohere scored 6.2%, whisper-medium 10.1% and Audar 18.9%, far
    better than on Perle, while stock large-v3 went the other way. Audar even copied ArzEn's
    `[LAUGHTER]` tags. ArzEn was dropped as evidence for these three, in favour of Perle and the real
    recordings.
24. Phone quality for the two leaders: Cohere went from 15.7% to 17.0% and whisper-medium from 17.6%
    to 20.4% (whole clip).
25. Both leaders on the test call with speaker labels. Cohere added notes such as `(تأتأة)`
    ("stutter") and `(غير مفهوم)` ("unclear") on the hesitant phone speech, which it never did on the
    benchmark clips, and seemed to drop phrases. The dropped phrases were later traced to a bug in
    our speaker path (see 28). The notes are now stripped automatically. whisper-medium gave the most
    complete transcript and kept English terms best, so it became the default engine.

## Second round: full-length runs, reviews and real meetings

26. The full 16-minute call with speaker labels. whisper-medium took 1.80 times real time with a
    2.9 GB peak (before the review fixes; about 0.5 after them, see 32). Cohere crashed: in one turn
    its decoder looped until it hit its length cap (`OutputTruncated`) and took the whole job down.
    Chunks that hit the cap are now split at their quietest point and retried, and a looped tail is
    trimmed. On the re-run Cohere finished at 0.37 times real time.
27. Free disk space fell to 3.9 GB (something outside this work had used about 5 GB), and the meeting
    data needed room. The R2T2 and Qwen3-ASR model files were deleted, since their benchmarks were
    done and they can be downloaded again.
28. An automated review of the code, the benchmark, the docs against the data, and privacy, with each
    finding re-checked by a skeptical second pass: 45 findings confirmed, 5 rejected. The most
    important: with speaker labels, the Cohere and llama engines never received 13–30% of the speech
    (up to about 46% on long calls). Others: the Arabic-word error metric charged English
    transliterations to Arabic; benchmark output on private meeting chunks, or audio in the project
    root, would not have been ignored by git; a Whisper gap-fill edge case could recurse forever; and
    nothing was saved until the end of a run. All were fixed, and the speaker path was rebuilt so
    that every pause-delimited chunk is transcribed, cut at speaker changes.
29. A critique of the benchmark: it scored a shortcut (the whole clip at once) rather than the
    pipeline actually recommended, had no confidence intervals, no telephone codec and no measurement
    of speaker labels. The benchmark now runs the `transcribe.py` pipeline, reports bootstrap
    confidence intervals, includes a G.711 telephone condition, records each run's settings, and
    uses pinned samples.
30. The three leaders re-measured on Perle at 16 kHz, through 8 kHz and through G.711, and on ArzEn.
    Cohere 13.4%, 14.7% and 15.3%; whisper-medium 18.5, 17.4 and 18.2; large-v3 with the hint 20.8,
    21.6 and 22.1. Cohere minus whisper-medium is −5.1 points with a 95% confidence interval of
    −10.9 to +0.8, which 648 reference words cannot settle. The gaps in Arabic-word errors and in
    English kept are clear.
31. A private set of real meetings, built read-only from saved meeting transcripts and the matching
    recordings: 6 meetings, 9 h 51 min, with ElevenLabs references checked to be verbatim and
    speakers corrected by hand. Two variants of one meeting turned out to be translations (English
    terms written in Arabic) and were set aside. The audio was cut exactly as it had been sent for
    transcription, so it lines up with the references. The set stays outside the repository.
32. Both leaders on 10-minute excerpts of each meeting, with speaker labels (WeSpeaker voiceprints).
    They disagreed with ElevenLabs on 47–49% of the words. Spot checks of the text suggest the
    differences are mostly local errors (English terms dropped or misspelled, phrases dropped), while
    ElevenLabs writes hesitations and variant spellings. A lenient score was added, which takes
    about 2 points off. whisper-medium kept more English (57% against 45%), and Cohere was twice as
    fast.
33. Speaker labels on the meetings: 25.7% of words went to the wrong person with WeSpeaker
    voiceprints, against 6.5% for ElevenLabs' raw labels. Five voiceprint models were compared on the
    same words. TitaNet-small got 11.7% and estimated the number of speakers correctly in 5 of 6
    meetings, and became the default.
34. Cohere without speaker labels on the meetings scored 4 points better (43.1% against 47.0%): the
    old voiceprints' false speaker changes had cut the audio into fragments. Re-run with TitaNet
    speaker labels, Cohere scored 43.8% with 10.9% of words on the wrong speaker, so labels now cost
    it under a point. It still kept fewer English words in Latin script than whisper-medium (46%
    against 57%).
35. Fresh runs on the 2-minute test call after all the fixes. Cohere now produced 284 words with
    speaker labels (248 before the fix, 292 without labels) and whisper-medium 310 either way, at
    0.3–0.6 times real time.
36. A final audit before publishing: every number in the docs checked against the result files,
    everything git would publish checked for private content, and a setup from a fresh clone. 33
    findings confirmed, 1 rejected. Most were numbers that had drifted. The "2–11% wrong speaker on
    2–3-person meetings" range left out a 2-person excerpt at 17%; the default engine's speed was
    quoted from before the fixes; the pooled share of English kept had been weighted by all words
    instead of English words (42% and 55% became 46% and 57%); and one excerpt was wrongly described
    as English-heavy. The audit also found meeting quotes and a meeting date in the docs, gaps in
    `.gitignore` (a benchmark run on a private set would not have been ignored), and a README command
    that ran large-v3 without its hint. All fixed: pooled meeting scores are exact totals, `results/`
    is an allowlist in `.gitignore`, `run_bench.py` writes private sets to the ignored
    `results/meetings/bench/` and gives large-v3 its hint as `transcribe.py` does, and
    `compare_voiceprints.py` skips models that are not downloaded.
37. Forced alignment for Cohere, tried after Sedjem was built. Cohere returns text without word
    times, so with speaker labels its audio is cut wherever the voice changes, and each piece loses
    the context around it. The alternative is to transcribe whole chunks and then align the text to
    the audio, so each word gets a time and its own speaker as with Whisper. This used
    ctc-forced-aligner with the MMS-300m forced-aligner model (1,130 languages; text romanized with
    uroman first) and skipped chunks where only one voice is heard, which gives the same result.
    On the six meeting excerpts, the text was the same as Cohere without speaker labels: 43.1% WER
    instead of 43.8%, and 32.7% errors on Arabic words instead of 38.3%. But English kept fell from
    46% to 39%, wrong-speaker words rose slightly from 10.9% to 11.9%, it ran at 0.43 times real time
    instead of 0.26 and peaked at 4.8 GB instead of 3.0 GB, and the aligner model's licence is
    non-commercial (CC-BY-NC-4.0). It stays an experimental option (`transcribe.py --align`), not the
    default.

38. The graphics card. transcribe.cpp ships a Vulkan backend, and it found the test laptop's
    integrated Iris Xe. Cohere ran there at 0.15 times real time instead of 0.36 on the Perle clips,
    with the same text for all 40 clips. The app now uses a graphics card when one works: Cohere through Vulkan on any GPU,
    a discrete one first; Whisper on NVIDIA GPUs through CUDA, after a download of NVIDIA's cuBLAS
    (about 600 MB, unpacked from NVIDIA's pip packages). Each is checked on a second of silence, any GPU
    error sends the rest of the recording to the CPU, and speed estimates now come from earlier runs on
    the same computer. In a first live test with the app, the 80-minute recording took 29.6 minutes
    with Cohere on the CPU (0.37 times real time).

## Tooling notes for whoever continues

- `pkill -f <pattern>` also matched the shell running the command and killed it. Use
  `pkill -x llama-server` or a `[p]attern` regex.
- Editing a shell script while it runs makes `sh` read garbage (`PROMPT: parameter not set`). The
  results were unaffected, but don't do it.
- Model names containing dots (`Qwen3-ASR-1.7B`) broke the result-file parser; `bench/score.py`
  handles them now.
- A fine-tuned Whisper folder without `tokenizer.json` makes faster-whisper fetch a tokenizer from
  Hugging Face (a download only; no audio is sent). The setup puts the file in the model folder.
- A bad model name made the old `run_configs.sh` wait forever for a server that never started. The
  runner now checks that the server is alive, times out, and validates names.
