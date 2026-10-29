# Recommendation

The job is transcripts of recorded meetings and calls in Egyptian Arabic mixed with English, with
speakers labelled by voice. Which model to use depends on what the transcript is for.

## What to use

For meeting minutes that people will rely on, use a hosted service. ElevenLabs Scribe v2 was the
reference in the project's tests: it has a free tier, costs $0.22/h after it, and has a setting
to stop it training on your audio. Speechmatics (`ar_en`, $100 of free credit, no training on your audio) is the other
option. A second benchmark ranks Scribe near the bottom on single-language Saudi speech, so try any
hosted service on one non-sensitive call first. Compress long WAV files to FLAC or Opus, since
several APIs cap uploads at 25 MB. And keep the transcript in its original language: a pipeline that
translates it into Arabic afterwards writes every English term in Arabic letters. In one full
meeting processed that way, only 1 of 4,149 words was left in Latin script.

For offline, private or free drafts, such as searchable notes, first drafts to correct, or
sensitive calls that must not leave the computer, there are two good local models:

- Cohere Transcribe Arabic (`--engine cohere`) has the lowest WER on the public set and on the
  meetings (43.8% against 48.8%, compared with ElevenLabs), the fewest errors on Arabic words, and
  the best speed, about 0.3 times real time, so an 80-minute call takes about 25 minutes. It is
  licensed Apache-2.0. Its weak spot is English: it keeps fewer English terms in Latin script (46%
  against 57% on the meetings, 77% against 90% on the public set), and the rest come out in Arabic
  letters, misheard or dropped.
- The whisper-medium code-switching fine-tune (the default) keeps English terms in English best
  (57% against 46% on the meetings, 90% against 77% on the public set) and gives word timestamps.
  It is MIT-licensed and runs at about 0.5 times real time.

Pick Cohere for Arabic-heavy meetings and for speed, and whisper-medium when the English terms matter
most. Either way, treat the output as a draft.

Whisper large-v3 with the one-sentence Egyptian style hint is the third local option. On the public
set it scored 20.8% and kept 88% of English terms, close to whisper-medium, but it is the slowest,
at about 1.5–2 times the length of the audio. The hint is what makes stock Whisper usable for this (with
the whole clip given to the model, it went from 39.7% to 22.4%), so use it where only stock Whisper
is available, for example a hosted Whisper service.

For speaker labels, the local voice clustering with TitaNet-small (the default) is good on most
meetings of two or three people: 2–11% of words went to the wrong person on four of five, and 17% on
the fifth, a 2-person excerpt where ElevenLabs' own labels were 27% off. `--speakers 0` estimated the
number of people correctly in 5 of 6 meetings. Larger meetings remain weak (38% on a 5-person one).
NVIDIA's Nemotron-3-Diarization, which can be chosen in Settings, halves that to 19% and counted
the people right in all 6 ([speaker labels](04-speaker-labels.md#nvidia-nemotron-3-diarization)).
On two public sets it also put the least speech on the wrong person: 2.2% against 2.5% for
TitaNet-small on ArzEn's Egyptian Arabic–English interviews, and 2.3% against 5.3% on AMI's English
meetings of four, at four to six times the time and with a count of the people that was right less
often ([public sets](04-speaker-labels.md#public-test-sets-arzen-and-ami)). So keep TitaNet-small
for meetings of two or three people, and choose Nemotron for four or more people or heavy crosstalk
([conclusions](04-speaker-labels.md#conclusions)).
pyannote `community-1`, which needs PyTorch and a free Hugging Face token, is untested.

## Real meetings are much harder than the benchmarks

On clean, read, single-speaker sentences (Perle), the best local models reach 13–18% WER. On the real
meetings, which are spontaneous, overlapping, recorded from video calls and have several people
talking, the same models disagree with ElevenLabs on 44–49% of the words
([results](03-results.md#real-meetings)). They drop English terms that ElevenLabs keeps, turn some
Egyptian phrases into formal Arabic, and mishear fast speech. Judging from the text of the
spot-checked stretches, most of the differences are the local models' errors. Their speaker labels,
on the other hand, are close to ElevenLabs': 11–12% of words go to the wrong person (8–9% pooled
over meetings of two or three people, 2–17% per meeting), against about 6% for ElevenLabs' own
labels.

## Next steps

1. Read the speaker labels on the test call (`results/poc/after-fixes/*.speakers.txt`).
2. Correct one 10-minute excerpt by hand. A human reference, even for one excerpt, would show how
   much of the disagreement with ElevenLabs is ElevenLabs' own error; so far that is only
   spot-checked.
3. Try a hybrid (untested): Cohere for Arabic and whisper-medium for stretches that are mostly
   English, with a language-ID step per chunk picking the engine.
4. Make use of the overlap Nemotron hears (untested). On AMI it found about half of the speech where
   two or more people talk, and was right 92% of the time when it heard that. Each voice of an
   overlap would have to be transcribed on its own.
5. Give Whisper a team's jargon with `--prompt` (plausible, untested).
6. Reclaim disk space: delete Audar (1.9 GB), the speaker models you don't use (Settings → Models
   deletes the ones it offers; the WeSpeaker models serve only `bench/compare_voiceprints.py`), the
   environment of `bench/check_nemotron.py` (`.venv-bench/`, 2.0 GB), the public speaker-label sets
   (`data/ami/` and `data/arzen-corpus/`, 3.3 GB), and whichever of the two local engines you don't
   use.

## For a product later

- CPU-only transcription is slow for long meetings. A small GPU or a hosted API turns hours into
  minutes.
- Licences: Cohere (Apache-2.0) and the whisper-medium fine-tune (MIT) allow commercial use, but
  check the fine-tune's training-data licence (its card mentions GPL). The MMS forced-aligner model
  behind `--align` is non-commercial (CC-BY-NC-4.0).

## Also tried

These were measured and are not usable for this job. All three hear the English words but write
most of them in Arabic letters.

- R2T2 (Confucius4-R2T2 from NetEase Youdao), the model the project started from: 47.1% WER on
  Perle with the whole clip, against 15.7% for Cohere measured the same way, and 25% of English
  terms kept. It runs on a CPU, but it is no better than its base model, and its strength,
  low-latency live streaming, doesn't matter for recordings. Its licence is a custom NetEase
  licence under PRC law.
- Qwen3-ASR-1.7B, the model R2T2 was fine-tuned from: 50.6% on Perle with the whole clip, and 8% of
  English terms kept.
- Audar-ASR-V1-Turbo: few errors on Arabic words (12.9%), but 43.5% WER on Perle with the whole clip
  and 12% of English terms kept. Its licence allows research and evaluation only.
