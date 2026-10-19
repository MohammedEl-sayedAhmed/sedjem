"""Tests that the media packages work together: faster-whisper's own audio loader (what Whisper models use
to read a recording) must work with the installed PyAV. PyAV 19 removed an argument faster-whisper 1.2.1
passes, which broke every Whisper transcription of a file; requirements.txt pins PyAV below 19 for that.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper.audio import decode_audio


class DecodeAudio(unittest.TestCase):
    def test_faster_whisper_reads_a_recording_with_the_installed_pyav(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.wav"
            t = np.arange(44100) / 44100
            sf.write(path, (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 44100)  # 1 s at 44.1 kHz
            audio = decode_audio(str(path), sampling_rate=16000)
        self.assertAlmostEqual(len(audio) / 16000, 1.0, delta=0.05, msg="resampled to 16 kHz, same length")
        self.assertGreater(float(np.abs(audio).max()), 0.2, "the tone survived, not silence")


if __name__ == "__main__":
    unittest.main()
