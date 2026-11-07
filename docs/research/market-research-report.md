# Egyptian Arabic–English call transcription with speaker labels: free-to-try recommendation (2026-09-25)

## 1. Top 3 free-to-try options

**1. ElevenLabs Scribe v2 (hosted): the reference to beat**
- **Why:** it is the only system with independent evidence on Egyptian Arabic–English code-switching. On Perle's benchmark it scored **13.1% WER against 45.9% for the next-best system** ([arXiv 2605.19069](https://arxiv.org/abs/2605.19069)).
  - The public Perle set is about 30% tech and software content and 36% work and meetings.
  - Scribe also has built-in diarization. `num_speakers` sets the maximum speaker count (1–32), and output includes word timestamps ([API ref](https://elevenlabs.io/docs/api-reference/speech-to-text/convert)).
  - On CallHome telephone audio, Scribe had the best speaker-attributed error (cpWER 14.82 / 16.18). That number comes from a competitor, is English only, and is published in [AssemblyAI's blog](https://www.assemblyai.com/blog/universal-3-5-pro-async).
- **Cost:** after the free allowance it costs $0.22/h. Starter is $6/mo and includes 27 h ([API pricing](https://elevenlabs.io/pricing/api)).
  - Two official pages disagree on the free allowance. The API page shows "4 h 30 min" of Scribe in its Free/PAYG column. The [plans page](https://elevenlabs.io/pricing) says 10k credits a month at 330 credits/min, which is about 30 min.
  - One job can use at most 2× the monthly quota ([help](https://elevenlabs.io/docs/help-center/account/general/is-there-a-limit-to-how-many-credits-i-can-use-at-once.md)). An 80-min call may not fit on the Free plan, so test a 15–20 min call first.
- **Risks:**
  - The Perle audio was recorded on headsets, not 8 kHz phone lines.
  - The Free plan is non-commercial and requires attribution.
  - Your audio is used to improve their models by default. You can opt out under Profile > Data use. Zero-retention mode is for Enterprise only ([help](https://elevenlabs.io/docs/help-center/legal/is-my-data-used-to-improve-eleven-labs-ai-models)).
  - The free tier can be disabled when you connect through a VPN or shared IP.

**2. Speechmatics, Enhanced model with `language='ar_en'` (hosted): largest free allowance, best privacy default**
- **Why:** it has an Arabic–English bilingual pack "for Arabic and English in the same file", and its Global Arabic model names Egypt ([languages](https://docs.speechmatics.com/speech-to-text/languages)). Diarization is included.
- **Cost:** **$100 credit with no card**, which is about 250 h of Enhanced at $0.40/h ([pricing](https://www.speechmatics.com/pricing)).
  - Your data is not used for training unless you opt in.
  - Batch data is deleted after 7 days.
- **Risks:**
  - All accuracy numbers are the vendor's own: 6.3% code-switching WER against Google's 9.7%, on an unnamed dataset ([post](https://www.speechmatics.com/company/articles-and-news/arabic-english-bilingual-speech-to-text)). Speechmatics was not in the Perle benchmark.
  - There is no speaker-count parameter, only `speaker_sensitivity`.
  - The docs don't say whether English words come out in Latin script.
  - Melia 1 is only Standard-level accuracy and has no custom dictionary ([models](https://docs.speechmatics.com/speech-to-text/models)).

**3. Local and private: Cohere Transcribe Arabic or Audar-ASR-V1-Turbo, with diarization run first**
- **Why:** these are the two strongest open Arabic models on the independent leaderboard.
  - Audar Turbo averages **23.17** and Cohere-Arabic **25.87**, against **36.86** for Whisper-v3 ([app.py](https://huggingface.co/spaces/elmresearchcenter/open_universal_arabic_asr_leaderboard/raw/main/app.py)).
  - Cohere reports its own numbers of Egyptian 19.16 and AR-EN code-switching 27.84, against 28.25 and 36.90 for Whisper-v3. It says English stays in Latin script ([blog](https://huggingface.co/blog/CohereLabs/cohere-transcribe-arabic-07-2026-release)).
  - Both have small GGUF downloads.
- **Cost:** free, and no audio leaves your computer.
- **Risks:**
  - Neither model outputs timestamps, so you must diarize first and transcribe each speaker turn.
  - Cohere's own model card says it "exhibits inconsistent performance on code-switched audio".
  - Audar's card says "heavily code-switched telephony… degrade[s] accuracy".
  - Neither has been measured on 8 kHz audio.

**Runner-up: Gemini 3.5 Transcribe.** It is free, lists `ar-EG` explicitly, claims intra-sentence code-switching, and returns `spk_1`/`spk_2` labels ([model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-transcribe)).
- It is a public preview.
- With diarization on, each request is capped at 30 min, so an 80-min call needs 3 chunks and you have to re-link the speakers across them.
- Word timestamps "may degrade accuracy" ([guide](https://ai.google.dev/gemini-api/docs/transcribe)).
- On the free tier your data is used to improve products and human reviewers may read it. The terms say "do not submit sensitive… information" ([pricing](https://ai.google.dev/gemini-api/docs/pricing), [terms](https://ai.google.dev/gemini-api/terms)).

## 2. Hosted options

| Service | Free allowance | Price after | Egyptian | Code-switching (English in Latin?) | Diarization | Data used for training? | Source |
|---|---|---|---|---|---|---|---|
| ElevenLabs Scribe v2 | 4.5 h or about 30 min/mo (pages conflict), no card | $0.22/h | Named on the Arabic page. Arabic tier is ≤20% WER in the docs, ≤25% on the Arabic page | Perle Egyptian 13.1% WER. The official Latin-script claim covers Indic–English only | Yes, up to 32 speakers, word timestamps | Yes by default (opt-out toggle) | [pricing](https://elevenlabs.io/pricing/api), [docs](https://elevenlabs.io/docs/overview/capabilities/speech-to-text), [Perle](https://arxiv.org/pdf/2605.19069) |
| Speechmatics | $100 credit, no card | $0.40/h Enhanced; $0.129/h Melia 1 | Egypt named | `ar_en` pack. Vendor claims 6.3% WER. Script not documented | Yes, no speaker count | No, unless you opt in | [pricing](https://www.speechmatics.com/pricing), [diarization](https://docs.speechmatics.com/speech-to-text/batch/batch-diarization) |
| Gemini 3.5 Transcribe | Free tier (limits shown only in AI Studio) | About $0.005/min | `ar-EG` explicit | Claimed. Script not documented. No benchmark | Up to 8 speakers. 30 min per request when diarizing | **Yes on free, plus human review** | [model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-transcribe) |
| AssemblyAI Universal-3.5 Pro | Up to 185 h, no card | $0.21/h + $0.02/h diarization | Arabic ("ar"). Egyptian named on a marketing page | "Native code switching", no Arabic example or WER | Yes | **Free users cannot opt out** | [pricing](https://www.assemblyai.com/pricing), [opt-out](https://www.assemblyai.com/docs/faq/how-to-opt-out-of-data-sharing-for-our-model-improvement-program) |
| Deepgram Nova-3 | $200, no card | $0.0043/min | `ar-EG` (monolingual) | **Arabic not in multi-language code-switching** | Included for pre-recorded | Per-request `mip_opt_out` (default not stated) | [pricing](https://deepgram.com/pricing), [languages](https://developers.deepgram.com/docs/models-languages-overview) |
| Groq Whisper large-v3 | 8 h audio/day, 25 MB/file | $0.111/h | Same model as your local Whisper | Mixed script, as you saw locally | None | No, and no retention by default | [limits](https://console.groq.com/docs/rate-limits), [data](https://console.groq.com/docs/your-data) |
| Cohere Transcribe Arabic API | Trial: 1,000 calls/mo, 5/min, 25 MB | Model Vault (sales) | Blog names Egyptian | Blog claims Latin-script English | None, no timestamps | Unclear for trial keys | [docs](https://docs.cohere.com/docs/transcribe-arabic), [limits](https://docs.cohere.com/docs/rate-limits) |
| Mistral Voxtral Transcribe V2 | Free mode, no card (whether Voxtral is included is unverified) | $0.003/min | Arabic, no dialect notes | Not documented | Yes, up to 3 h per request | Opt-out toggle, default unknown | [news](https://mistral.ai/news/voxtral-transcribe-2/) |
| OpenAI gpt-4o-transcribe-diarize | None | About $0.006/min | Perle ≈45.9% (bar-chart attribution) | Diarize model takes no prompt | Yes | No | [guide](https://developers.openai.com/api/docs/guides/speech-to-text) |
| Azure Speech F0 | 5 h/mo real-time only. Card needed for account | $0.18/h batch | `ar-EG` | Language ID "doesn't support changing languages within the same sentence". Perle ≈54.6% | Yes (S0) | Not checked | [pricing](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/) |
| Google Chirp 3 | $300 credit (card) | $0.016/min | `ar-EG` preview | Perle 59.7% | **No Arabic diarization** | Not checked | [chirp-3](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3) |
| Gladia Solaria-1 | €50 one-time (about 80 h) | $0.61/h | Arabic | `code_switching` with `['ar','en']` | Yes | Default on Starter (secondary source) | [pricing](https://www.gladia.io/pricing) |
| Hamsa | 50 min | $5/mo | Egyptian named | Claimed | Claimed | Unknown | [docs](https://docs.tryhamsa.com/overview/capabilities/speech-to-text) |

Not recommended: Amazon Transcribe has no `ar-EG` ([docs](https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html)). Soniox no longer gives free credits ([blog](https://soniox.com/blog/2025-10-27-free-credits-update-for-soniox-api)).

## 3. Open-weights options

| Model (HF id) | Size / download | CPU runtime | Egyptian / code-switch evidence | Reported WER (dataset) | License | Source |
|---|---|---|---|---|---|---|
| `CohereLabs/cohere-transcribe-arabic-07-2026` (gated). GGUF: `handy-computer/cohere-transcribe-arabic-07-2026-gguf` | 2B. Q4_K_M 1.56 GB, Q8_0 2.41 GB | transcribe.cpp (≤400 s per call); CrispASR `cstr/…-GGUF` | Vendor-internal: Egyptian 19.16, AR-EN code-switching 27.84 | Leaderboard avg 25.87 (#2), Casablanca 49.71 | Apache-2.0 | [card](https://huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026), [tc.cpp](https://raw.githubusercontent.com/handy-computer/transcribe.cpp/main/docs/models/cohere-transcribe-arabic-07-2026.md) |
| `audarai/Audar-ASR-V1-Turbo` | 2.35B. Q4_K_M 1.28 GB / Q8_0 2.17 GB, plus BF16 mmproj 0.64 GB | llama.cpp `llama-mtmd-cli`, 30 s window | Egyptian and code-switching in training. No Egyptian number | Leaderboard avg 23.17 (#1), Casablanca 47.02 | AudarAI Community (research and evaluation OK) | [card](https://huggingface.co/audarai/Audar-ASR-V1-Turbo) |
| `audarai/Audar-ASR-V1-Flash` | 0.78B. 0.40 / 0.64 GB, plus 0.38 GB mmproj | Same | Same claims | Avg 32.04 | AudarAI Open | [card](https://huggingface.co/audarai/Audar-ASR-V1-Flash) |
| `mohammedaly22/QwenCleo-ASR` | 1.7B, **4.08 GB bf16 only, no GGUF** | `qwen-asr` (the card documents GPU only). GGUF conversion unverified | Egyptian podcasts, keeps English in Latin script | Self-reported 19.85 / 10.52 CER on its own 3,699-utterance set. On that set: Qwen3 base 41.51, Whisper-v3 63.94 | Apache-2.0 | [card](https://huggingface.co/mohammedaly22/QwenCleo-ASR) |
| `openai/whisper-large-v3` (baseline) | 1.55B | faster-whisper int8 | Arab Voices Egyptian (arz) 35.69 (2nd of 14) | Leaderboard 36.86 | Apache-2.0 | [Arab Voices](https://arxiv.org/html/2601.13319v2) |
| `Seif-Eldeen-Sameh/whisper-medium-arabic-codeswitched-ct2` | 775 MB CT2 int8 | faster-whisper, drop-in | Egyptian lectures; "keeps code-switching intact" | 17.9% on its own held-out slice (base 52.4%) | MIT (training data GPL) | [card](https://huggingface.co/Seif-Eldeen-Sameh/whisper-medium-arabic-codeswitched-ct2) |
| `MohamedRashad/Arabic-Whisper-CodeSwitching-Edition` | 1.55B (CT2 port: `Mano200600/faster-whisper-large-v2-ar-codeswitching`) | faster-whisper | English in Latin script. **Training data derived from ArzEn** | 37.98 on the QwenCleo set | GPL-3.0 | [card](https://huggingface.co/MohamedRashad/Arabic-Whisper-CodeSwitching-Edition) |
| `ahmedheakl/arazn-whisper-medium` | 3.06 GB safetensors | Convert to CT2 | Trained on the ArzEn-ST train split | ArzEn-ST test 31.1 / 12.0 CER | MIT | [paper](https://arxiv.org/pdf/2406.18120v2) |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | Q8 GGUF 742 MB | NeMo-Speech.cpp, CrispASR | `ar-AR` only; **transliterates English** (يوتيوب) | 38.04 on the QwenCleo set; leaderboard 35.86 | OpenMDW-1.1 | [card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) |
| omniASR-LLM-Unlimited-300M-v2 (`cstr/omniasr-llm-unlimited-300m-v2-GGUF`) | q4_k 1.07 GB | CrispASR | `arz_Arab` supported. No code-switching evidence | Arab Voices arz 41.7 | Apache-2.0 | [gguf](https://huggingface.co/cstr/omniasr-llm-unlimited-300m-v2-GGUF) |
| MBZUAI ArTST v3 joint | fairseq checkpoint, 1.85 GB | fairseq (awkward to set up) | Trained on ArzEn | ArzEn 27.43 | CC-BY-NC-4.0 | [ACL 2025](https://aclanthology.org/2025.acl-long.1427.pdf) |

Skip these:
- **VibeVoice-ASR:** leaderboard 52.99, 5 GB download.
- **Gemma-4-E4B:** 32.98, but 4.41 GB.
- **Metro-ASR-Small:** its card contradicts itself (28.15 vs 46.85 WER).
- **MAdel121 Egyptian Whispers:** Latin letters are stripped from their training data, so they cannot output English in Latin script.
- **Qwen3-ForcedAligner:** does not support Arabic ([README](https://raw.githubusercontent.com/QwenLM/Qwen3-ASR/main/README.md)).

## 4. Diarization

**Best free local option: pyannote `speaker-diarization-community-1`**
- Settings: `num_speakers=2`, then use `output.exclusive_speaker_diarization`.
- Performance: 26.7% DER on CALLHOME part 2, no collar, overlap scored ([card](https://huggingface.co/pyannote/speaker-diarization-community-1)).
- Setup:
  - Install torch from the CPU index (about 184 MB, not the 555 MB default).
  - Set `PYANNOTE_METRICS_ENABLED=0`. Telemetry is on by default ([config](https://raw.githubusercontent.com/pyannote/pyannote-audio/main/src/pyannote/audio/telemetry/config.yaml)).
- Merging with Whisper: take faster-whisper `word_timestamps=True`, give each word the speaker active at its midpoint, then merge consecutive words into turns.

**Local alternatives trained on telephone audio**
- **NVIDIA Streaming Sortformer v2.1:** 5.68 DER on 2-speaker CALLHOME.
- **Nemotron-3-Diarization:** 5.98 DER on 2-speaker CALLHOME ([card](https://huggingface.co/nvidia/Nemotron-3-Diarization)).
- The NVIDIA figures use a 0.25 s collar, so they cannot be compared with pyannote's.
- Both run in NeMo-Speech.cpp, which resamples 8 kHz audio itself. Neither can be forced to 2 speakers.
- The prebuilt v0.1.0 release documents Sortformer v2 only, so Nemotron-3 may need a source build.
- **Fastest option without torch:** sherpa-onnx with segmentation-3.0 int8, TitaNet-L and `num_clusters=2` ([docs](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html)). TitaNet was trained on telephone speech; your WeSpeaker VoxCeleb embeddings were not.

**Best hosted option**
- pyannoteAI precision-2 scores 16.6% DER on CALLHOME (same no-collar protocol as the 26.7% above). It was best overall in ETH's CallHome benchmark (11.2 average; [arXiv 2509.26177](https://arxiv.org/html/2509.26177v1)).
- It offers a 1-month free trial with no card. The page says both "150 h" and "170 h" ([pricing](https://www.pyannote.ai/pricing)).
- ElevenLabs' built-in diarization is the simpler all-in-one choice.

## 5. Local test shortlist

Test first on the public, ungated Perle Egyptian set ([HF](https://huggingface.co/datasets/Perle-ai/ASR_Code_Switch), MIT; downloading only) and on your own calls. Several models trained on ArzEn-derived data, so your ArzEn clips may inflate their scores.

| # | Repo | File(s) | Runtime | Expected speed |
|---|---|---|---|---|
| 1 | `handy-computer/cohere-transcribe-arabic-07-2026-gguf` | Q8_0 (2.41 GB) or Q4_K_M (1.56 GB) | transcribe.cpp, `language=ar`, ≤400 s segments (or CrispASR `cstr/…-GGUF`, `--backend cohere-ar`) | About 4–4.6× faster than real time on a Ryzen 7 4750U, measured on English clips; unmeasured on Arabic or your i5 |
| 2 | `audarai/Audar-ASR-V1-Turbo` | Q4_K_M GGUF (1.28 GB) + BF16 mmproj (0.64 GB) | Your existing llama.cpp `llama-mtmd-cli`, ≤30 s turns | Similar to your Qwen3-ASR (~0.7× real time); estimate. Flash (≈1 GB total) is the faster fallback |
| 3 | `mohammedaly22/QwenCleo-ASR` | `model.safetensors` (4.08 GB, over budget, about 45 min) | llama.cpp `convert_hf_to_gguf.py` → Q8, or `qwen-asr` on CPU | About ~0.7× real time if conversion works (unverified) |
| 4 | `Seif-Eldeen-Sameh/whisper-medium-arabic-codeswitched-ct2` | `model.bin` (775 MB) | faster-whisper `compute_type=int8` | RTF 2.03 on 2 vCPU; likely faster than your large-v3 on 10 cores |

**Will any beat Whisper large-v3?**
- On paper, #1 and #2 should beat it on Egyptian WER and on keeping English in Latin script. The leaderboard gap is 23–26 vs 36.9, and Cohere's own tests show 19.2 vs 28.3 on Egyptian and 27.8 vs 36.9 on code-switching.
- #3 claims a large win (19.85 vs 63.94), but that set rewards Latin-script output. On the Arabic-only subset Whisper scores 49.25.
- Against that, on the independent Egyptian pool Whisper-v3 (35.69) is near the top, and nothing has been measured on 8 kHz audio. So a win is likely but not proven.
- None of them is expected to reach Scribe's level.

## 6. Unverified points and caveats

- **No benchmark on 8 kHz audio.** No source measures any model or API on 8 kHz Arabic–English calls. Perle used headset MP3 and the leaderboard uses 16 kHz non-telephone audio.
- **ElevenLabs free allowance is unclear.** The official pages conflict (4.5 h vs about 30 min), and the per-job cap may block an 80-min file.
- **Perle attributions are partly inferred.** The paper text names only ElevenLabs (13.1%) and Chirp 3 (59.7%). The 45.9% (gpt-4o-transcribe) and about 54.6% (Azure) figures are read from its bar chart. Perle is a data vendor and the paper is not peer-reviewed.
- **Vendor-only numbers.** Cohere's Egyptian and code-switching figures come from unnamed internal test sets. Speechmatics' code-switching WERs are vendor-only. Audar's "independently reproduced" claim is Audar's own statement.
- **QwenCleo:** the test set is single-author and possibly in-domain. Both the GGUF conversion and CPU use are untested.
- **Contamination risk.** MohamedRashad's code-switching dataset is ArzEn-derived ([card](https://huggingface.co/datasets/MohamedRashad/arabic-english-code-switching)). The same risk applies to arazn-whisper and ArTST, which trained on ArzEn.
- **CPU speed estimates.** The transcribe.cpp speed was measured on English clips on a Ryzen. Audar and QwenCleo speeds are estimates. Pyannote has no official CPU speed figure.
- **NVIDIA diarizers:** the Nemotron-3-Diarization release date (2026-09-23) is not confirmed, and it may not be in the prebuilt NeMo-Speech.cpp. Neither Nemotron nor Sortformer can be forced to 2 speakers.
- **Other unconfirmed items:**
  - Whether Mistral's free mode includes Voxtral.
  - Whether Cohere trial keys are excluded from training.
  - Deepgram's training-program default.
  - Hamsa API access on its free plan.
  - Groq's no-card free tier.
- **Script handling is undocumented.** Whether Gemini, Speechmatics and AssemblyAI keep English in Latin script inside Arabic speech is not documented.