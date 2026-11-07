# Handoff: can R2T2 transcribe Arabic + English meetings?

This is the note the project started from, kept as a historical record; the conclusions for the use case are in [the results](../03-results.md) and [the recommendation](../07-recommendation.md).

Written 2026-09-24 by Claude. The previous session stopped at the usage limit, partway through a hands-on test.

## The question

The user wants to transcribe **recorded audio**, for example tech meetings spoken mainly in **Arabic** with **English tech words and whole English sentences mixed in**. That mix is called Arabic–English code-switching. It's for an **MVP and personal trials**, not commercial use. They know ElevenLabs Scribe does this, but they have no ElevenLabs key. They want to know whether **R2T2 (https://r2t2.ai/)** can do it instead.

## What R2T2 is (verified)

- R2T2 is "Confucius4-R2T2" from NetEase Youdao.
- It only turns speech into text. There's no text-to-speech, no hosted API and no pricing. You get open weights to run yourself, plus a free web demo.
- It's a fine-tune of `Qwen/Qwen3-ASR-1.7B`, which is Apache-2.0.
- Links:
  - Code: https://github.com/netease-youdao/Confucius4-R2T2. The repo was created 2026-09-08, the first commit was 2026-09-16, and it has 4 commits. Version 0.1.0, about 600 stars.
  - Weights: https://huggingface.co/netease-youdao/Confucius4-R2T2 (4.1 GB, bf16 safetensors).
  - GGUF: https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF. Model sizes are Q4_K_M 1.1 GB, Q8_0 1.8 GB and f16 3.4 GB. The mmproj file is 348 MB at Q8_0 or 642 MB at f16.
  - Demo: https://r2t2.youdao.com/demo
- It's built for **low-latency live streaming**. Text is only ever appended, never revised. Average latency is 200–600 ms, with chunks from 80 ms to 2 s. None of this helps with recorded meetings.

## Findings that matter for this use case

1. **Trained only on Chinese and English.**
   - A maintainer said so in GitHub issue #1 on 2026-09-18: "The current model was trained only on Chinese and English, but we noticed some cross-lingual capability on the additional languages".
   - Someone asked about Urdu and Arabic in that thread. There was no answer as of 2026-09-24.
   - The README lists Arabic only under "retains useful cross-lingual capability". No Arabic benchmark is published.
2. **No speaker diarization.** Timestamps are available only in offline mode, through Qwen3's forced aligner (`return_time_stamps`).
3. **The accuracy claims are self-reported and cover English and Chinese only.**
   - The eval scripts aren't released, and the tech report is "coming soon".
   - In the README's own table at 160 ms, R2T2 is not the best system.
   - LibriSpeech-clean WER: R2T2 2.13, AssemblyAI 1.89, "Commercial B" 1.25.
   - Earnings22 WER: R2T2 9.36, AssemblyAI 7.47.
4. **Known bug, from GitHub issue #2.**
   - It drops the last 1–2 words at the end of a file. The maintainers' workaround is to append about 0.5 s of silence.
   - A user in that issue couldn't reproduce the published WenetSpeech CER. The eval script is still unreleased.
5. **Hardware.**
   - The official path needs an NVIDIA GPU with vLLM. `qwen-asr[vllm]` pins `vllm==0.14.0` and `transformers==4.57.6`.
   - Streaming re-sends **all the audio so far** through the model on every chunk (see `r2t2/r2t2_asr.py`, around line 322). That's heavy on CPU.
   - The prebuilt llama.cpp `.so` files in the repo need CUDA (`libcuda.so.1`) and CPython 3.12.
   - Upstream llama.cpp does support Qwen3-ASR audio (`qwen3a` in mtmd). So official llama.cpp builds plus the GGUF files **should** run it on CPU. **Not verified yet.**
6. **License for the weights: "NetEase Youdao Model Use License Agreement".**
   - It's royalty-free. A separate license is needed only above 100M monthly active users or RMB 1B annual revenue.
   - It bans using the model or its outputs to improve other AI models, except non-commercial ones. It also bans high-risk uses.
   - Youdao can revoke it if the terms are breached. It's governed by PRC law, with disputes arbitrated by CIETAC in Beijing.
   - **Fine for an MVP or personal trials.** The code itself is Apache-2.0.
7. **Security notes.**
   - `ws_server.py` binds to `0.0.0.0` by default. A quick look found no authentication on `/asr_stream_api_v1`. Don't expose it to the internet.
   - The README's Docker command mounts `/var/run/docker.sock`, which gives the container root control of the host. Don't copy that.
   - The r2t2.youdao.com demo sends audio to Youdao's servers. **Don't upload private meeting audio there** without the user's OK.

## Preliminary verdict (not yet confirmed by a hands-on test)

R2T2 is a **poor fit** for transcribing Arabic + English meetings, for four reasons:

- Arabic isn't in its training data.
- Its streaming strength doesn't matter for recordings.
- It has no diarization.
- It needs a GPU.

If an open-weights model is wanted, test the **base `Qwen3-ASR-1.7B`** (Apache-2.0) and **Whisper large-v3** next to it. Check Qwen3-ASR's model card to confirm it officially supports Arabic.

The previous session also started web research on paid and free options: the ElevenLabs Scribe free tier, Gemini, OpenAI gpt-4o-transcribe, Soniox, Speechmatics and others. **It didn't finish, and nothing from it is verified.** Redo it if it's needed.

## Next steps: the hands-on test that was interrupted

1. **Get test audio.**
   - Best: 1–3 minutes of the user's own meeting audio, kept on the local machine.
   - Public Arabic–English code-switched data: `sqrk/mixat-tri` on Hugging Face (ungated). The `test` split has `audio`, `transcript` and `language` fields. `language == "CS"` marks code-switched clips, and English words appear in `[brackets]`.
   - Fetch rows from `https://datasets-server.huggingface.co/rows?dataset=sqrk/mixat-tri&config=default&split=test&offset=0&length=100`
   - Other datasets:
     - `arbml/ESCWA`: UN meetings in Arabic and English. Train split only.
     - `ahmedsamirtarjama/MBZUAI_ArzEn_wav`: Egyptian Arabic and English. Train and validation splits.
     - `MBZUAI/MIXAT`
2. **Compare three models.**
   - R2T2: `Confucius4-R2T2-Q8_0.gguf` with `mmproj-Confucius4-R2T2-Q8_0.gguf`.
   - Its base model: `ggml-org/Qwen3-ASR-1.7B-GGUF`.
   - Whisper large-v3, through whisper.cpp or faster-whisper.
3. **Run the models with llama.cpp server**, bound to localhost only. The image below is the CPU one. On a GPU machine use `:server-cuda` and add `--gpus all`.
   ```bash
   docker run --rm -p 127.0.0.1:8080:8080 -v ~/models:/models ghcr.io/ggml-org/llama.cpp:server \
     -m /models/Confucius4-R2T2-Q8_0.gguf --mmproj /models/mmproj-Confucius4-R2T2-Q8_0.gguf \
     --host 0.0.0.0 --port 8080 -c 8192
   ```
   - Send a request like this to `/v1/chat/completions`:
     `{"messages":[{"role":"user","content":[{"type":"input_audio","input_audio":{"data":"<base64 16kHz mono WAV>","format":"wav"}}]}],"temperature":0}`
   - The model replies `language X<asr_text>TEXT`. Strip that prefix.
   - For the exact prompt, see `r2t2_llama/llama_native_backend.py` (`LlamaServerClient` and `build_asr_prompt`). It also shows how to force the language with `language Arabic<asr_text>`.
   - Pad 0.5 s of silence onto the end of each clip.
   - On an NVIDIA GPU you can use the official path instead. Clone the repo, run `pip install -e .` under Python 3.12, then run `./run_example.sh clip.wav --model_path netease-youdao/Confucius4-R2T2 --infer_mode onetime_vllm --language Arabic`.
4. **Score the results.**
   - Compute WER and CER with jiwer, after normalizing the Arabic: strip diacritics and tatweel, map أ/إ/آ to ا and ى to ي, remove punctuation and the `[ ]` brackets, and lowercase the Latin text.
   - Also check **whether English words come out in Latin script or get transliterated or translated into Arabic**. That's the key failure mode for this use case.
5. **Report** a table per model with a clear recommendation.

## Paste this into the new Claude session

> Read R2T2-ARABIC-TRANSCRIPTION-HANDOFF.md. My use case: transcribe recorded meetings spoken mainly in Arabic with English tech words and sentences mixed in, for an MVP and personal trials. Continue from "Next steps": run the hands-on comparison (R2T2 vs base Qwen3-ASR-1.7B vs Whisper large-v3) on Arabic–English code-switched audio, then give me a clear recommendation, including the best free or cheap options if R2T2 isn't good enough. Don't upload my audio to any external service without asking me.
