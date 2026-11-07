# Disk space and cleanup

What the project puts on disk, and how to remove it. Everything except the code, the docs and the
public-set results is ignored by git.

## Inside the project folder

| Path | Size | What |
|---|---|---|
| `.venv/` | about 0.5 GB | Python 3.12 with faster-whisper, sherpa-onnx, transcribe-cpp, jiwer, soundfile, numpy and requests. The optional forced aligner adds PyTorch (CPU build) and transformers, about 1 GB more |
| `tools/llama/` | 41 MB | prebuilt llama.cpp CPU binaries (release b11165), only for the Qwen3-ASR family |
| `models/whisper-medium-arabic-codeswitched-ct2/` | 0.8 GB | the default engine, plus the Whisper tokenizer so it runs offline |
| `models/cohere-transcribe-arabic-07-2026-gguf/` | 1.6 GB | Cohere Transcribe Arabic, Q4_K_M |
| `models/Audar-ASR-V1-Turbo/` | 1.9 GB | Audar Turbo Q4_K_M and its BF16 audio encoder |
| `models/diarization/` | 0.3 GB, 0.7 GB with Nemotron | speaker models. `--speakers` needs only TitaNet-small (the default). TitaNet-large and 3D-Speaker CAM++ can also be chosen in the app (*Settings → Models*), which downloads Nemotron-3-Diarization into `Nemotron-3-Diarization/` here when asked; WeSpeaker ResNet34 (the first default) and ResNet152 are used only by `bench/compare_voiceprints.py`; there is also an unused pyannote segmentation model |
| `models/mms-300m-1130-forced-aligner/` | 1.3 GB | the optional forced-aligner model for `--align` |
| `.venv-bench/` | 2.0 GB | an environment with PyTorch and Transformers, used by `bench/check_nemotron.py` to compare `nemotron.py` with NVIDIA's own implementation, and with pyarrow added, by the speaker models' public-set benchmarks (`bench/compare_ami.py`, `bench/compare_arzen.py`) |
| `data/` | about 0.4 GB without the two folders below | public test clips (Perle, ArzEn, Mixat) in three audio conditions, chunks of the test calls, and the 10-minute meeting excerpts |
| `data/ami/` | 1.9 GB | AMI's 16 test meetings for `bench/compare_ami.py`: the audio (1.0 GB), the files downloaded from Hugging Face (`download/`, 0.9 GB, needed only until the meetings are unpacked) and Nemotron's saved probabilities (`nemotron/`, 50 MB) |
| `data/arzen-corpus/` | 1.4 GB | the ArzEn corpus for `bench/compare_arzen.py`, with Nemotron's saved probabilities (`nemotron/`, 67 MB). The zip it comes in (1.1 GB) isn't needed once it is unzipped |
| `results/poc/`, `results/meetings/` | small | transcripts of the private calls and meetings |
| `app_data/` | about 50 MB per hour of recordings | the app's transcriptions (16 kHz audio copies, transcripts, logs) and saved API keys. Delete a transcription in the app, or the whole folder |

The R2T2 and Qwen3-ASR model files (4.7 GB) were deleted once their benchmarks were done, when free
disk space fell to 3.9 GB. `bench/fetch_models.py` with no arguments downloads them again. Audar
(1.9 GB) can go too if you don't plan to use it.

The desktop build keeps its data somewhere else: `%LOCALAPPDATA%\Sedjem` on Windows and
`~/.local/share/sedjem` on Linux ([desktop app](10-desktop.md#where-things-are-kept)).
Uninstalling leaves that folder in place, so delete it by hand to remove the models and
transcriptions.

## Outside the project folder

Stock Whisper large-v3 loads from the Hugging Face cache (`~/.cache/huggingface`, about 3 GB) when
it isn't in `models/`. On the test laptop it was already there and was reused.

The tests ran in the performance power profile, because power-saver kept the CPU at 0.4–1.2 GHz.
The profile was switched back afterwards with `powerprofilesctl set power-saver`. The app can do the
same switch while local jobs run (`performance_while_running` in [the app](09-app.md)).

## Privacy

- No audio was sent to any outside service. Models ran locally, and the llama.cpp server listened
  on `127.0.0.1` only.
- Network use was downloads only: models, the public test sets, the meeting recordings from their
  own storage (read-only), and a Whisper tokenizer file.
- The market research used public web pages only, with no accounts, keys or uploads.
- The repository contains no audio, no meeting data and no transcripts of the private calls or
  meetings. The docs report only aggregate numbers for them.
