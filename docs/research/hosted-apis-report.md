# Hosted Arabic–English speech-to-text: second, independent research pass

A single research agent produced this report (2026-09-24/25) separately from the 8-agent workflow in
[market-research-report.md](market-research-report.md). It used public pages only: no uploads,
no accounts. Prices are list prices for batch transcription in USD per audio hour.

## Comparison

| Provider / model | $/h batch | Free tier | Arabic dialects | Arabic–English code-switching | Diarization | Max file | Trains on your data? |
|---|---|---|---|---|---|---|---|
| **ElevenLabs Scribe v2** | 0.22 (+0.05 keyterms, +0.07 entities) | API Free/PAYG: 4 h 30 min/month | "Arabic (ara)"; no dialect list | yes, detects languages automatically within one file | yes, up to 32 speakers | 3 GB / 10 h | **yes by default**; opt out under "Data use"; zero retention on enterprise only |
| **OpenAI** gpt-transcribe (new) / gpt-4o-transcribe / 4o-mini / whisper-1 / 4o-transcribe-diarize | 0.27 / 0.36 / 0.18 / 0.36 / 0.36 | none | not published | gpt-transcribe accepts several `languages` hints "for multilingual audio and code-switching" | only the -diarize model | 25 MB; the 4o models cap output at 2K tokens | **no**; the transcriptions endpoint keeps no abuse logs and is ZDR-eligible |
| **Gemini 3.5 Transcribe** | ≈0.30 | free tier (limits shown only in AI Studio) | locale list includes only ar-EG | yes, within and between sentences | up to 8 speakers | 1 h, or **30 min** with diarization/timestamps | **free tier: yes, and human reviewers may read it**; paid: no |
| **Google Cloud Chirp 3** | 0.96; 0.18 with dynamic batch | $300 credit for 90 days | 18 locales incl. ar-SA/AE/EG, **all in preview** | no ("transcribes in the most prevalent language") | not for Arabic | ≈1 h | no |
| **Azure Speech**: batch / fast / LLM Speech / **MAI-Transcribe-2 (preview)** | 0.18 / 0.36 / 0.36 / **0.10 promo until 31 Dec 2026** | F0: 5 h/month, real-time only | 18 locales incl. ar-EG | MAI-2 switches automatically (Arabic unverified); the fast multilingual model has no Arabic | batch yes (≤240 min) | batch 1 GB | real-time and fast are not stored |
| **AWS Transcribe** | 0.36 | 60 min/month for 12 months (may not apply to new accounts) | ar-AE, ar-SA; **no Egyptian** | language ID per segment | yes | 4 h / 2 GB | **yes by default**; opt-out policy needed |
| **Deepgram Nova-3** | 0.258 | $200 credit, no card | 17 variants incl. EG | **no, for Arabic** | yes | 2 GB | **yes by default** unless `mip_opt_out=true` on every request |
| **AssemblyAI Universal-3.5 Pro** | 0.21 (+0.02 diarization) | up to 185 h, no card | not published | yes, natively | +$0.02/h | 5 GB / 10 h | **yes by default; free users cannot opt out** |
| **Soniox** | ≈0.10 | **none** (free credits ended 27 Oct 2025) | no formal list | yes, natively | yes | 300 min | **never**; files deleted after 30 days |
| **Speechmatics**: Melia 1 / Standard / Enhanced | 0.129 / 0.24 / 0.40 | **$100 credit, no card** | `ar` covers MSA, Gulf, Egyptian, Levantine; **`ar_en` bilingual pack** | yes (`ar_en`, or Melia 1's automatic switching) | yes | <1 GB upload | **no** unless you opt in (for 33% off); batch data deleted after 7 days |
| **Gladia Solaria-1** | 0.61 | €50 one-time | `ar` | yes (`code_switching`) | yes | 135 min / 1000 MB | free/Starter: yes by default |
| **Rev.ai** Reverb Foreign Language | 0.30 | ≈5 h of credit | `ar` | not published | not verified | not published | says it never trains external LLMs; own training unverified |
| **Groq** whisper-large-v3 / turbo | 0.111 / 0.04 | 8 h of audio/day | Whisper Arabic | no | no | 25 MB free | not retained by default |
| **Together** Whisper large-v3 | 0.09 | not published | Whisper | no | not published | not published | training opt-in; outputs stored by default |
| **Mistral Voxtral Mini Transcribe V2** | 0.18 | not verified | Arabic (1 of 13 languages) | not documented | yes | 3 h per request | free mode may train (opt-out) |
| **Audar API** Turbo / Pro / +diarization | 0.48 / 0.84 / 1.08 (rolling out Sept 2026) | not published | MSA, Gulf, Egyptian, Levantine, Maghrebi | yes, trained for it | separate model | not published | not published |
| **Munsit** (CNTXT, UAE) | ≈0.96–3.00 | ≈10 min, no card | "25+ dialects" (self-reported) | claimed | not published | not published | not published |
| **Cohere Transcribe Arabic** API | trial free; production via Model Vault (price not published) | 1,000 calls/month | Arabic dialects + English | docs say yes; model card says "inconsistent" | no, and no timestamps | 25 MB | logs kept 30 days; opt-out toggle |

**File sizes:** an 80-minute 8 kHz 16-bit mono WAV is about 77 MB, which is over the 25 MB caps at
OpenAI, Groq's free tier and Cohere. Transcode to FLAC or Opus, or split it. Gemini Transcribe
(30 min with speaker labels), Chirp 3 (≈1 h) and Gladia (135 min) also need long meetings split.

## Arabic accuracy evidence

**Independent**

- **Perle code-switching benchmark** ([arXiv 2605.19069](https://arxiv.org/abs/2605.19069), May 2026):
  300 utterances per language pair (Saudi–English, Egyptian–English, Persian–English, German–English),
  recorded on headsets. Mean WER over the 4 pairs: Scribe v2 **13.2%**, gpt-4o-transcribe 38.6%,
  Chirp 3 39.4%, Azure 43.6%. Scribe was lowest on both Arabic pairs.
- **GigaSpeechBench** ([arXiv 2606.28884](https://arxiv.org/abs/2606.28884)): in-the-wild audio,
  **one language per clip**; Alibaba is a co-author. Saudi WER: Chirp 3 16.8, Azure 20.1,
  Gemini 3.0 Flash 20.1, Deepgram 25.0, Qwen3-ASR-1.7B 25.9, Whisper-v3 32.8, **Scribe v2 33.3**,
  GPT-4o Transcribe 42.4. **The ranking flips** compared with the code-switching benchmark.
- **Open Universal Arabic ASR Leaderboard** (Elm): open models only, single-language 16 kHz sets.

**Self-reported only:** Speechmatics `ar_en` 6.3% vs Google 9.7%; Deepgram "up to ~40% lower WER";
ElevenLabs puts Arabic in its 10–20% tier; AssemblyAI in its 10–25% tier. **No source tested 8 kHz.**

## Picks from this pass

1. **Best quality:** ElevenLabs Scribe v2 — the only hosted service with independent evidence of
   winning on Arabic–English code-switching. Switch off "Data use" training first.
2. **Cheapest with good quality:** Speechmatics Melia 1 ($0.129/h) or `ar_en` Standard ($0.24/h);
   Soniox (≈$0.10/h) has no free credit. Neither is independently tested.
3. **Best free option:** Speechmatics' $100 credit (≈775 h of Melia 1), with ElevenLabs' free
   4.5 h/month as the quality reference. Avoid AssemblyAI's and Gemini's free tiers for real meetings.
4. **Most privacy-friendly hosted:** Soniox (never trains, deletes after 30 days) or OpenAI
   gpt-transcribe (no training, no abuse logs for transcription; Arabic quality untested).

## Open-weights models noted (leaderboard average WER / SADA Saudi WER, Aug 2026)

`audarai/Audar-ASR-V1-Turbo` 23.17 / 28.92 · `CohereLabs/cohere-transcribe-arabic-07-2026` 25.87 / 37.47 ·
`facebook/omniASR-LLM-1B` 29.96 / 43.84 · `audarai/Audar-ASR-V1-Flash` 32.04 / 44.36 ·
`nvidia/stt_ar_fastconformer_hybrid_large_pcd_v1.0` 32.91 / 44.52 · `Qwen/Qwen3-ASR-1.7B` 33.36 / 45.53 ·
`nvidia/nemotron-3.5-asr-streaming-0.6b` 35.86 / 53.06 · `openai/whisper-large-v3` 36.86 / 55.96.
Community: `IbrahimAmin/code-switched-egyptian-arabic-whisper-small` (Egyptian–English, no
independent WER). Canary-1B-v2 and Parakeet-TDT-0.6B-v3 have no Arabic.

## Not verified from an official source

- **ElevenLabs:** whether the free plan needs no card, and its Arabic dialect coverage.
- **OpenAI:** gpt-transcribe's Arabic quality, and its maximum duration beyond 25 MB.
- **Gemini:** free-tier limits for Transcribe, and dialects beyond ar-EG.
- **Azure:** whether MAI-Transcribe-2 handles Arabic–English code-switching.
- **AWS:** the free tier for new accounts.
- **Deepgram:** whether the opt-out costs extra.
- **Fireworks:** its current audio pricing.
- **Rev.ai:** its own-model training policy.
- **Mistral:** Voxtral in free mode, and code-switching.
- **Gladia:** whether Starter can opt out itself.
- **Soniox:** its dialect list and code-switching quality.
- **Munsit:** its "#1" claim (it is not on the current leaderboard).
- **Audar:** API pricing and data policy.
- **Cohere:** code-switching quality.
- **All vendor WER figures** are self-reported.
