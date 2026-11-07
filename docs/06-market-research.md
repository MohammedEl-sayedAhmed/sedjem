# Market research: hosted and open models

This is a summary. The full report with sources is
[research/market-research-report.md](research/market-research-report.md). The raw material is in
[research/raw-sweeps.json](research/raw-sweeps.json) (142 candidates from 5 searches) and
[research/verification.json](research/verification.json) (58 claims re-checked against official
pages: 39 confirmed and 19 corrected, with the corrections applied in the report).

## How it was done

The research ran on 25 September 2026, on the public web only: no audio uploaded, no sign-ups, no
API keys. Five independent searches, none seeing the others' results, covered general open models,
Arabic-specialised models and fine-tunes, published accuracy evidence, hosted free tiers, and
diarization. Two verification passes then re-checked the claims the recommendation depends on
against official sources, and a final step ranked the options.

## Findings

ElevenLabs Scribe v2 is the only system with independent evidence on Egyptian Arabic–English. It
scored 13.1% WER on the Egyptian–English subset of Perle's benchmark
([arXiv 2605.19069](https://arxiv.org/abs/2605.19069)), against about 45.9% for the next best,
gpt-4o-transcribe (read from the paper's chart). Averaged over all four language pairs it was 13.2%
against 38.6%. The public set is about 30% tech and software talk. These numbers use the paper's own
clips and normalization, so they can't be compared directly with this project's local scores.
Scribe includes diarization and word timestamps. The free allowance is unclear (the official pages
say 4.5 h or about 30 min a month), and it costs $0.22/h after that. It uses your audio for training
unless you opt out under Profile, then Data use.

Speechmatics has an `ar_en` bilingual pack and gives $100 of credit without a card (about 250 h). It
doesn't train on your data unless you opt in, deletes batch files after 7 days, and includes
diarization. Its accuracy numbers are the vendor's own.

Gemini 3.5 Transcribe is free, lists Egyptian Arabic (`ar-EG`) and labels speakers. But the free
tier uses your audio and allows human review, and requests with diarization are capped at 30 minutes.

AssemblyAI gives 185 h free and Deepgram $200. Free AssemblyAI users can't opt out of training, and
Deepgram's code-switching doesn't cover Arabic.

AWS Transcribe has no Egyptian Arabic, Google Chirp 3 has no Arabic diarization (and 59.7% on
Perle), and Soniox no longer gives free credit, so none of the three suits this.

Among open models, the independent Open Universal Arabic ASR Leaderboard ranks Audar-ASR-V1-Turbo
(23.17) and Cohere Transcribe Arabic (25.87) far ahead of Whisper large-v3 (36.86). Several Whisper
fine-tunes target Egyptian code-switching; some were trained on ArzEn, which is why ArzEn is not a
fair test for them.

Nothing public measures any system on 8 kHz Arabic phone calls. The tests in this project are the
only evidence for that condition.

## Hosted options side by side

| Service | Free to try | Egyptian | Code-switching | Speakers | Trains on your audio? |
|---|---|---|---|---|---|
| ElevenLabs Scribe v2 | 30 min–4.5 h a month | yes | best measured (13.1%) | yes | yes, unless you opt out |
| Speechmatics | $100 credit, no card | Egypt named | `ar_en` pack | yes | no, unless you opt in |
| Gemini 3.5 Transcribe | free tier | `ar-EG` | claimed | yes (up to 8) | yes, on the free tier |
| AssemblyAI | up to 185 h | "ar" | claimed | yes | free users can't opt out |
| Deepgram Nova-3 | $200 | `ar-EG` | no Arabic code-switching | yes | opt-out per request |
| Groq (hosted Whisper large-v3) | 8 h a day | as local Whisper | as local Whisper | no | no |

The full report has the prices, sources and caveats, and the table of open models.

## A second, independent pass

A separate single-pass review ([research/hosted-apis-report.md](research/hosted-apis-report.md))
covered 19 hosted services. It agrees with the picks above and adds a few points.

The evidence on ElevenLabs conflicts. On GigaSpeechBench (arXiv 2606.28884; recordings from the
wild, one language per clip), Scribe v2 ranks near the bottom for Saudi Arabic, with 33.3 WER
against 16.8 for Google Chirp 3. Its lead is specifically on code-switched speech, which is what
these meetings are, but a test on your own audio should decide. The API pricing page lists ElevenLabs' free
API tier as 4 h 30 min a month.

There are newer or cheaper options. OpenAI's `gpt-transcribe` costs $0.27/h, takes several language
hints for code-switched audio and doesn't train on your data, but has no free tier, and its Arabic
quality is untested. Azure MAI-Transcribe-2 is in preview at a promotional $0.10/h until 31 December
2026, with Arabic code-switching unverified. Speechmatics Melia 1 costs $0.129/h, which stretches the
$100 credit to about 775 h.

For privacy, Soniox (about $0.10/h) never trains on your audio and deletes files after 30 days, but
it no longer gives free credit.

File size matters. An 80-minute 8 kHz WAV is about 77 MB, over the 25 MB upload caps at OpenAI,
Groq's free tier and Cohere. Convert it to FLAC or Opus, or split it, first.

Two more open models are worth trying: `facebook/omniASR-LLM-1B` (Apache-2.0, 29.96 on the
leaderboard) and `IbrahimAmin/code-switched-egyptian-arabic-whisper-small` (Egyptian–English, 0.24B
parameters).
