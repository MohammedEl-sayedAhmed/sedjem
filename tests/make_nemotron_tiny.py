#!/usr/bin/env python3
"""Make tests/nemotron-tiny/: a tiny random Nemotron-3-Diarization and what Transformers makes of a test
signal with it, so tests/test_nemotron.py can check nemotron.py without PyTorch.

The model has the real one's layout and spectrogram but 2 layers of width 16, 4 speakers, and chunks,
queue and speaker cache small enough that the 20 s signal goes through 21 chunks and many cache
compressions. Its weights are random (seed 0), with the output layer scaled up so that the speakers'
probabilities spread over 0 to 1 instead of all sitting near 0.5.

It needs PyTorch and Transformers: .venv-bench/bin/python tests/make_nemotron_tiny.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
from transformers import Nemotron3DiarizationConfig, Nemotron3DiarizationForAudioFrameClassification

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_nemotron import TINY, signal  # noqa: E402

REAL = HERE.parent / "models" / "diarization" / "Nemotron-3-Diarization"


def main():
    torch.manual_seed(0)
    config = Nemotron3DiarizationConfig(
        audio_config={"hidden_size": 16, "num_attention_heads": 2, "num_key_value_heads": 2, "num_hidden_layers": 2,
                      "intermediate_size": 32, "num_mel_bins": 128, "subsampling_factor": 8},
        head_config={"audio_hidden_size": 16, "hidden_size": 8, "num_speakers": 4, "subsampling_factor": 8},
        streaming_config={"num_speakers": 4, "speaker_cache_length": 16, "fifo_length": 4,
                          "speaker_cache_update_period": 6, "speaker_cache_silence_frames_per_speaker": 1,
                          "subsampling_factor": 8},
        chunk_length=12, chunk_right_context=3, fifo_length=4, speaker_cache_update_period=6)
    model = Nemotron3DiarizationForAudioFrameClassification(config).eval()
    with torch.no_grad():
        for p in model.parameters():
            p.normal_(0, 0.2)
        model.classifier.out_proj.weight.mul_(10)
    TINY.mkdir(exist_ok=True)
    model.save_pretrained(TINY)
    (TINY / "processor_config.json").write_bytes((REAL / "processor_config.json").read_bytes())

    from transformers import AutoProcessor
    processor = AutoProcessor.from_pretrained(TINY)
    with torch.inference_mode():
        probs = model(**processor(signal(), sampling_rate=16000)).logits.sigmoid()[0].numpy()
    np.save(TINY / "expected.npy", probs.astype(np.float32))
    for name in ("generation_config.json",):
        (TINY / name).unlink(missing_ok=True)
    print(f"{TINY}: {len(probs)} frames, probabilities from {probs.min():.3f} to {probs.max():.3f}, "
          f"{np.mean(probs > 0.5):.0%} above 0.5; config {json.loads((TINY / 'config.json').read_text())['model_type']}")


if __name__ == "__main__":
    main()
