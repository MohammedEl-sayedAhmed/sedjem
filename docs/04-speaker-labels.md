# Speaker labels

None of the speech-to-text models in the project's tests label speakers, so telling speakers apart
(diarization) is a separate step that runs locally, in `speakers.py`.

## How it works

1. Find the speech. Silero VAD marks where anyone is talking.
2. Take voiceprints. A 1.5-second window slides over the speech every 0.75 s. On very long
   recordings the step grows so that there are at most 2,000 windows; if many short turns still
   exceed that, an evenly spaced subset is clustered and every other window joins the nearest
   speaker. For each window a speaker-embedding model, by default NVIDIA TitaNet-small (40 MB,
   trained with telephone speech among other data), produces a voiceprint that captures timbre and
   pitch: what a voice sounds like, not what it says.
3. Group by voice. Spectral clustering (NME-SC, Park et al. 2019) groups the voiceprints into N
   speakers. The neighbour count is tuned for each recording, so a handful of odd windows (noise,
   crosstalk, laughter) can't become a speaker of their own. N comes from `--speakers N`, and
   `--speakers 0` estimates it from the eigengap.
4. Smooth. Within each speech segment, a window whose two neighbours agree with each other takes
   their speaker.
5. Attribute the text. Whisper returns a timestamp for every word, so each word goes to the speaker
   whose voice is active at that moment, and consecutive words from one speaker form a line. Cohere
   and the Qwen3-ASR family give no word timestamps. For them, every pause-delimited chunk is cut
   where the voice timeline changes speaker (snapped to the quietest 20 ms within ±0.3 s; pieces
   under 0.3 s join a neighbour), and each piece is transcribed and labelled. Either way, all the
   speech is transcribed. With `--align`, Cohere's words get times from forced alignment instead
   ([below](#forced-alignment-experimental)).

Everything runs on the CPU with sherpa-onnx and a small ONNX voiceprint model: no PyTorch, no
account, no upload. Speaker labels add a few seconds, about 2–3 s on the 2-minute test call
(whisper-medium took 68.4 s without them and 70.8 s with them). With TitaNet-small the voiceprints
take 4–8 s per 10-minute excerpt, plus the clustering. `--voiceprint-model` swaps in another model.

## What was tried, and what went wrong

| Version | Result | Problem |
|---|---|---|
| sherpa-onnx's built-in pipeline, counting speakers itself | 4 speakers on the test call (69 s, 49 s, 5 s, 2 s) | invented two tiny speakers |
| the same, told there were 2 | 111 s against 14 s | merged both real voices into Speaker 1; Whisper invented text for the fragments left to Speaker 2 |
| voiceprints and spectral clustering, first version | 57 s against 44 s, turns alternate | The code review found a bug: for the Cohere and llama engines, the speaker turns covered only the middle 0.75 s of each window, so 13–30% of the speech (up to about 46% on long calls, where the window step grows) was never transcribed. It also switched smoothing off on long recordings and misreported talk time |
| fixed version, WeSpeaker ResNet34 voiceprints | 58 s against 44 s on the test call; every sample of speech reaches the model | 25.7% of words credited to the wrong person on the meeting excerpts |
| current version, TitaNet-small voiceprints | | 11.7% wrong on the meeting excerpts; number of speakers estimated right in 5 of 6 |

The failure with 2 speakers is a known weakness of agglomerative clustering. When a few outlier
segments are further from everyone than the two real voices are from each other, the two voices
merge first.

## Accuracy on real meetings

This was measured on the six 10-minute meeting excerpts, with 2–5 people each (see
[the results](03-results.md#real-meetings)), as WDER: the share of words credited to the wrong
person. Our words are aligned to the hand-corrected ElevenLabs reference, and our Speaker 1, 2 and
so on are matched to the real people in the way that agrees best. `bench/compare_voiceprints.py`
relabels the same Whisper words with each voiceprint model, so only the speaker step differs.

| Voiceprint model | Size | Wrong speaker, true count given | Wrong speaker, count estimated (`--speakers 0`) | Count estimated right |
|---|---|---|---|---|
| TitaNet-small (default) | 40 MB | 11.7% | 10.2% | 5 of 6 |
| 3D-Speaker CAM++ (Chinese and English) | 28 MB | 11.7% | 10.9% | 4 of 6 |
| TitaNet-large | 101 MB | 12.2% | 10.0% | 5 of 6 |
| WeSpeaker ResNet34 (the first default) | 26 MB | 25.7% | 22.9% | 2 of 6 |
| WeSpeaker ResNet152 | 79 MB | 36.9% | 29.9% | 3 of 6 |

![Paired horizontal bars of the share of words given to the wrong speaker by five voiceprint models, with the true number of speakers given and with it estimated, and a column counting how often the estimate was right](images/charts/voiceprints.svg)

*Words credited to the wrong person with each voiceprint model, given the true number of speakers or estimating it; the right column counts the excerpts where the estimate was right.*

Per excerpt with TitaNet-small and the true count: 4.6% (2 people), 7.8%, 10.4% and 2.6% (3 people
each), 16.5% (2 people) and 38.4% (5 people).

For comparison, ElevenLabs' own raw labels differ from the hand corrections on 6.5% of the words
over the same stretches, and on 5.9% over the five 2–3-person excerpts, where TitaNet-small gets
9.0%. That comparison favours ElevenLabs, since the corrections started from its labels. With
TitaNet, the local labels come within a few points of ElevenLabs on meetings of two or three
people, and remain weak on larger meetings.

![Paired horizontal bars for each meeting excerpt, m1 to m6, and two pooled rows, of the share of words given to the wrong speaker by TitaNet-small with the true number of speakers and by ElevenLabs' own labels](images/charts/speakers-per-excerpt.svg)

*Per excerpt, TitaNet-small against ElevenLabs' raw labels, both scored against the hand corrections; the ElevenLabs figures per excerpt are in [the results](03-results.md#real-meetings).*

The two voiceprint families trained on VoxCeleb (celebrity interviews) do worst, and the models
trained with telephone and large multi-speaker data (TitaNet, 3D-Speaker) do best, as the research
predicted. With TitaNet the estimate missed only on the 5-person excerpt. There it found fewer
speakers than were present (2 of 5 with TitaNet-small, 3 with TitaNet-large) and still scored better
than clustering into the true 5 (21.6% against 38.4%, and 16.0% against 41.0%). So on larger
meetings `--speakers 0` is a reasonable choice even when the headcount is known. The other models
also missed on 2–3-person excerpts, and not always by finding too few: CAM++ found 3 speakers for 2
people on one excerpt and scored worse there (19.0% against 17.0% with the true count).

## NVIDIA Nemotron-3-Diarization

NVIDIA's Nemotron-3-Diarization (September 2026, about 100M parameters, OpenMDW 1.1 licence, which
allows commercial use) replaces all of the steps above with one model. Every 10 ms it gives the
probability that each of up to 8 speakers is talking, so it also hears when two people talk at once.
Arabic is not among the languages its model card lists. It can be chosen instead of TitaNet-small in
Settings → Models, or with `--voiceprint-model models/diarization/Nemotron-3-Diarization` on the
command line.

Sedjem runs NVIDIA's weights with NumPy alone (`nemotron.py`), following the Transformers
implementation step by step: a log-mel spectrogram, 8 of its 10 ms frames stacked into one, then the
recording in chunks of 27.2 s, each seen together with a cache of up to 264 earlier frames that keeps
the clearest ones of every speaker, so the speaker numbers hold from one chunk to the next. On the six
excerpts its probabilities stayed within 0.000007 of Transformers' on five and within 0.015 on the
sixth, and not one 10 ms frame changed who was talking (`bench/check_nemotron.py`, which needs
PyTorch and Transformers; `tests/test_nemotron.py` checks a tiny random model the same way without
them).

The probabilities become the same kind of timeline as the voiceprints: in 80 ms steps, speech is where
someone's probability is above 0.5, and it goes to the most likely speaker, with the same 3-step
smoothing. Each word goes to the speaker most likely over the whole word, which put 10.0% of the words
on the wrong person against 10.2% when only the middle of the word counts. With the true count N
given, only the N speakers it heard longest get any. `bench/compare_nemotron.py` labels the same
Whisper words this way and with TitaNet-small in the same run, so only the speaker step differs.

| Speaker labels | Size | Wrong speaker, true count given | Wrong speaker, count estimated | Count estimated right | Seconds per 10-minute excerpt |
|---|---|---|---|---|---|
| TitaNet-small voiceprints and clustering | 40 MB | 11.7% | 10.2% | 5 of 6 | 5 |
| Nemotron-3-Diarization | 397 MB | 10.0% | 10.0% | 6 of 6 | 30 |

It counted the people right on all six excerpts, including the 5-person one where TitaNet-small found
2, so giving it the true count changed nothing. On that excerpt it put 19.4% of the words on the
wrong person, against 38.4% for TitaNet-small with the true count and 21.6% with its estimate. On the
five excerpts of two or three people the two are level at 9.0% pooled: per excerpt 2.7%, 8.4%,
10.0%, 3.2% and 17.7% for Nemotron, against 4.6%, 7.8%, 10.4%, 2.6% and 16.5%.

It hears two or more people at once in 4.0% of the speech (0.6–8.0% per excerpt). Sedjem gives every
word one speaker, and so does the reference, so this is not an advantage yet: of the 169 words said
in those stretches, Nemotron's most likely speaker was the wrong one for 29.6%, against 26.6% for
TitaNet-small with the true count and 20.1% with its estimate. Making use of the overlap would mean
transcribing each voice of an overlap separately.

The times are for the speaker step alone, with 4 threads on the test laptop in the balanced power
mode, on 28 October 2026. Transformers with PyTorch took 14 s for the same work, so NumPy is about
twice as slow; with all 12 threads, NumPy's default, it was twice as slow again, so `nemotron.py`
keeps it to 4. Both models are small next to transcription: Whisper took 0.53 times real time on these
excerpts in the performance mode, about 320 s each. The Nemotron step peaked at 0.65 GB of memory.

TitaNet-small stays the default: it is a tenth of the size and six times faster, and level with
Nemotron on meetings of two or three people. Nemotron is the better choice for larger meetings, where
it counted the people right and put half as many words on the wrong person. It hears at most 8
speakers.

## Public test sets: ArzEn and AMI

The meeting excerpts above are private, so the speaker models were also scored on two public sets
that anyone can download, with `bench/compare_arzen.py` and `bench/compare_ami.py`
([how to run them](05-command-line.md#benchmark)):

- ArzEn (Hamed, Vu and Abdennadher, LREC 2020; CC BY-SA 4.0): 38 informal interviews held at the
  German University in Cairo, 12.1 hours of Egyptian Arabic mixed with English, at 16 kHz. Each has
  one interviewee and the same two interviewers, a man and a woman (two recordings have only one of
  them). The corpus marks who speaks only through its transcription's segments, which never overlap
  and run from one cut to the next, so the pauses inside them, and often the silence before the next
  speaker, count as speech.
- AMI (CC BY 4.0): English meetings, many of the speakers not native. Its standard test set is 16
  meetings, 9.1 hours, 15 of them of four people and one of three, from headset microphones mixed
  into one channel, with the reference turns taken from the word timings (the `ihm` set of
  `diarizers-community/ami` on Hugging Face, at a pinned revision). Two or more people talk at once
  in 14.6% of the speech, which is 27.0% of the speaker time.

Both are scored on 10 ms frames, with each model's speakers matched one to one to the real people in
the way that agrees best, by two measures:

- Wrong speaker on speech, which is closest to how Sedjem uses the labels. The speech model
  transcribes wherever anyone talks, and each word gets the speaker of the timeline at that moment,
  also where the speaker model heard no one. So every 10 ms of the reference's speech is labelled
  that way, and this is the share of it that goes to someone who isn't talking then. It is the
  time-based counterpart of the words on the wrong person above.
- DER (diarization error rate), the usual measure: the share of the reference's speaker time that
  is missed, falsely detected or given to the wrong person, summed over the speakers, so an overlap
  of two people counts twice. Nothing is forgiven around the turns (no collar) and overlaps are
  scored, the strictest setup; published AMI figures use several others, so compare them with care.
  ArzEn's turns include pauses, so its DER is also given with a 0.25 s collar: the frames within
  0.25 s of a speaker starting or stopping are not scored, which leaves 96% of its speech.

Nemotron has two rows. As the app uses it, it gives one speaker at a time (the timeline described
above). Its own output, everyone above 0.5, includes the overlaps but gives no single speaker for a
word, so it has no wrong-speaker figure. The tables are for the count estimated (`--speakers 0`).
The time is for the whole speaker step, per 10 minutes of audio, with 4 threads on the test laptop
in the balanced power mode, on 29 October 2026.

### ArzEn: interviews in Egyptian Arabic and English

| Speaker labels | Wrong speaker on speech | DER | Missed / false alarm / confusion | DER with a 0.25 s collar | Count estimated right | Seconds per 10 minutes |
|---|---|---|---|---|---|---|
| TitaNet-small voiceprints (default) | 2.5% | 13.4% | 9.8% / 2.3% / 1.3% | 11.3% | 38 of 38 | 8 |
| TitaNet-large voiceprints | 2.5% | 13.4% | 9.8% / 2.3% / 1.3% | 11.3% | 38 of 38 | 19 |
| 3D-Speaker CAM++ voiceprints | 2.6% | 13.5% | 9.8% / 2.3% / 1.3% | 11.3% | 38 of 38 | 11 |
| Nemotron, one speaker at a time (as the app) | 2.2% | 21.4% | 18.6% / 1.9% / 0.9% | 19.2% | 29 of 38 | 29 |
| Nemotron, overlaps included | | 21.6% | 18.7% / 2.0% / 0.9% | 19.4% | 29 of 38 | 29 |

The three voiceprint models score alike, count the people right in every interview, and score the
same with the count given. Nemotron put a little less of the speech on the wrong person (2.2%
against 2.5%, and 1.5% against 1.7% with the collar) and confused people less (0.9% against 1.3%).
Its DER is higher because it hears no one in 18.7% of the reference's speech, where Silero VAD,
which finds the speech for the voiceprints, hears no one in 9.8%. The reference counts pauses as
speech, so part of that is pauses, and the app still gives every word there a speaker. In 9
interviews it heard a fourth voice, for 0.6 to 5 seconds, which makes the count wrong but changes
little else.

One interview, R14, holds 10–12% of every model's wrong-speaker time. TitaNet-small and Nemotron
each put about 13% of its speech on someone other than the reference does, and they agree with each
other on 99% of it: mostly, both give to an interviewer what the reference gives to the interviewee.
That points to mistakes in the reference rather than in the models; without R14, TitaNet-small is at
2.3% and Nemotron at 2.0%. The interviews also share their interviewers, so the set has 40 voices,
and every recording asks the same of the models: two interviewers, a man and a woman, and one
interviewee. ArzEn marks no overlap, so it can't show what Nemotron's overlap is worth. AMI can.

### AMI: English meetings with much overlap

| Speaker labels | Wrong speaker on speech | DER | Missed / false alarm / confusion | Speaker time missed in overlaps | Count estimated right | Seconds per 10 minutes |
|---|---|---|---|---|---|---|
| TitaNet-small voiceprints (default) | 5.3% | 29.3% | 24.4% / 1.7% / 3.2% | 55.6% | 15 of 16 | 6 |
| TitaNet-large voiceprints | 4.2% | 28.5% | 24.4% / 1.7% / 2.4% | 55.6% | 15 of 16 | 15 |
| 3D-Speaker CAM++ voiceprints | 4.8% | 29.0% | 24.4% / 1.7% / 2.9% | 55.6% | 14 of 16 | 9 |
| Nemotron, one speaker at a time (as the app) | 2.3% | 31.6% | 30.5% / 0.6% / 0.5% | 58.4% | 14 of 16 | 34 |
| Nemotron, overlaps included | | 26.0% | 24.0% / 1.2% / 0.7% | 34.6% | 14 of 16 | 34 |

Overlap is what makes AMI hard. A timeline with one speaker at a time misses at least one voice
wherever two people talk, and that is most of what the voiceprint models miss: 15.0% of the speaker
time, against 9.3% where one person talks. As the app uses it, Nemotron put about half as much of
the speech on the wrong person as the voiceprint models (2.3% against 4.2–5.3%), with about a fifth
of their confusion, but it misses more of the speech where one person talks (14.7% of the speaker
time, against 9.3%), so its DER is the highest. Its own output, overlaps included, has the lowest
DER, 26.0%, and misses a third of the speaker time in overlaps instead of more than half: when it
hears two or more people at once it is right 91.5% of the time, and it hears 48.3% of the overlap.
With the count given, the voiceprint models put 4.5% (TitaNet-small), 5.4% (TitaNet-large) and 4.8%
(CAM++) of the speech on the wrong person, and Nemotron 2.3% again. Every model got the count wrong
on one or two meetings.

### What the public sets show

They agree with the meeting excerpts. With the count estimated, Nemotron put the least speech on the
wrong person on all three: 10.0% of the words on the excerpts against 10.2% for TitaNet-small, 2.2%
of the speech on ArzEn against 2.5%, and 2.3% on AMI against 5.3%. Its lead grows with the number of
people and the overlap, from about level on interviews and on meetings of two or three people to
about half the errors on AMI's meetings of four, and it is the only model that hears overlap.
Against that, it takes four to six times as long as TitaNet-small, and its count of the people was
right less often on the public sets (29 of 38 interviews and 14 of 16 meetings, against 38 and 15
for TitaNet-small), though it counted the 5-person excerpt right where TitaNet-small found 2. So
TitaNet-small stays the default, and Nemotron is the better choice for meetings of four or more
people, and where people often talk over each other.

## Forced alignment (experimental)

Cutting Cohere's audio at every speaker change means each piece is transcribed without the words
around it. The alternative is to transcribe the whole pause-delimited chunk and then find where each
word falls in the audio, which is what forced alignment does. `align.py` does this with
[ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner) and the MMS-300m
forced-aligner model (`MahmoudAshraf/mms-300m-1130-forced-aligner`, 1,130 languages, 1.26 GB). The
model reads romanized text, so the Arabic and the English words mixed into it are both romanized
with uroman first. Each word then gets the speaker whose voice is active at its midpoint, as with
Whisper. A chunk where the voice timeline shows a single speaker skips the alignment, because every
word in it gets that speaker anyway. If an alignment fails, the chunk's words are spread evenly over
it.

On the six meeting excerpts the aligned labels put 11.9% of words on the wrong speaker, against
10.9% with the audio cut at speaker changes, and the text is the same as Cohere's without labels
(43.1% WER against 43.8%). The costs: English kept falls from 46% to 39%, processing goes from 0.26
to 0.43 times real time and peak memory from 3.0 to 4.8 GB, and it needs PyTorch. The aligner model
is licensed CC-BY-NC-4.0, for non-commercial use only. For those reasons it is not the default. The
full comparison is in [the results](03-results.md#variations-on-cohere), and the setup in
[the command-line notes](05-command-line.md#forced-alignment-experimental).

## Output format

```
[mm:ss-mm:ss] Speaker 1: …
[mm:ss-mm:ss] Speaker 2: …
```

With Whisper (and with `--align`), `<name>.<model>.speakers.words.json` also lists every word with
its start, end and speaker, so speaker models can be compared without transcribing again
(`bench/compare_voiceprints.py`).

## Limits

- Boundaries are accurate to about 0.75 s (one window step), so a word at a hand-over can land on
  the wrong side.
- Overlapping speech goes to one speaker only.
- Similar voices on one channel are hard. On the 2-minute test call the two speakers' average
  WeSpeaker voiceprints have a cosine similarity of 0.90 (1.0 is identical). TitaNet-small's are at
  0.48, though scores from different models can't be compared directly.
- Larger meetings are hard: 38% of words were misattributed on the 5-person excerpt even with
  TitaNet (19% with Nemotron-3-Diarization).
- The reference labels used for scoring were corrected by hand from ElevenLabs output and are not
  perfect either, especially for short interjections.

## Known upgrades, not tested by the project

- pyannote `speaker-diarization-community-1` with `num_speakers`: 26.7% DER on CALLHOME (no
  collar). It needs PyTorch (the CPU build is about 184 MB) and a free Hugging Face token.
- NVIDIA Sortformer v2.1: 5.68% DER on 2-speaker CALLHOME (0.25 s collar) through NeMo-Speech.cpp.
  It can't be told the number of speakers.
- Hosted: ElevenLabs Scribe has diarization built in, and pyannoteAI offers a free trial month.
