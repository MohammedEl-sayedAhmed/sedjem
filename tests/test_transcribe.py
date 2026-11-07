"""Tests for transcribe.py's speaker attribution with forced alignment, using stand-ins for the
speech model, the aligner and the voice timeline (no models needed).
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import align  # noqa: E402
import transcribe  # noqa: E402

SR = transcribe.SR


class TextOnly:
    """Like Cohere: text without word timestamps."""
    name = "text-only"
    prompt = None

    def __call__(self, audio):
        return "one two three four"


class FakeAligner:
    """Splits the chunk (including its trailing silence) into four equal words."""
    name = "fake-aligner"
    failures = 0
    spread = staticmethod(align.Aligner.spread)

    def __init__(self):
        self.calls = 0

    def words(self, audio, text):
        self.calls += 1
        q = len(audio) / SR / 4
        return [(i * q, (i + 1) * q, w) for i, w in enumerate(text.split())]


class Timeline:
    """Speaker 1 until 3 s, then speaker 2."""
    def speaker_at(self, t):
        return 1 if t < 3 else 2

    def speaker_over(self, a, b):
        return self.speaker_at((a + b) / 2)

    def changes(self, a, b):
        return [3.0] if a < 3 < b else []


class AlignedTest(unittest.TestCase):
    def test_spread(self):
        self.assertEqual(align.Aligner.spread("a b c", 3.0), [(0.0, 1.0, "a"), (1.0, 2.0, "b"), (2.0, 3.0, "c")])
        self.assertEqual(align.Aligner.spread("", 3.0), [])

    def test_only_chunks_with_a_speaker_change_are_aligned(self):
        aligner = FakeAligner()
        engine = transcribe.Aligned(TextOnly(), aligner)
        chunks = [(0, 2 * SR), (2 * SR, 4 * SR)]  # one voice throughout, then a change at 3 s
        with mock.patch.object(transcribe, "split_at_pauses", return_value=chunks):
            lines, words = transcribe.speaker_lines(engine, np.zeros(4 * SR, np.float32), Timeline())
        self.assertEqual((engine.skipped, aligner.calls), (1, 1))
        self.assertEqual([w[3] for w in words], [1, 1, 1, 1, 1, 1, 2, 2])
        self.assertEqual([(x["speaker"], x["text"]) for x in lines],
                         [(1, "one two three four one two"), (2, "three four")])

    def test_names_and_engine_attributes(self):
        engine = transcribe.Aligned(TextOnly(), FakeAligner())
        self.assertEqual(engine.name, "text-only-aligned")
        self.assertIsNone(engine.prompt)
        self.assertEqual(engine(np.zeros(SR, np.float32)), "one two three four")


if __name__ == "__main__":
    unittest.main()
