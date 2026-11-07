"""nemotron.py runs Nemotron-3-Diarization in NumPy as Transformers does, and speakers.py turns its
probabilities into the Timeline the transcription uses.

The model here is tests/nemotron-tiny/, a tiny random one made with Transformers by
tests/make_nemotron_tiny.py together with Transformers' output for signal(). The real model was
checked the same way on six 10-minute meetings (bench/check_nemotron.py): the probabilities differed
by at most a few millionths.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nemotron  # noqa: E402
import speakers  # noqa: E402

TINY = Path(__file__).resolve().parent / "nemotron-tiny"


def signal(seconds=20, sr=16000):
    """Three tones with harmonics taking turns, sometimes at once, then a pause: 20 s, so that the last
    10 ms frame is alone in its 80 ms group and masked."""
    t = np.arange(seconds * sr) / sr
    out = np.zeros_like(t)
    for f0, (a, b) in ((140, (0.5, 7)), (220, (6, 12.5)), (310, (12, 16)), (140, (15.5, 18))):
        on = (t >= a) & (t < b)
        out[on] += sum(np.sin(2 * np.pi * f0 * k * t[on]) / k for k in (1, 2, 3)) * 0.2
    return out.astype(np.float32)


def probs_of(*turns, frames=600, speakers_=4):
    """Probabilities every 10 ms: 0.9 for (speaker, first frame, last frame) turns, else 0.05."""
    p = np.full((frames, speakers_), 0.05, np.float32)
    for s, a, b in turns:
        p[a:b, s] = 0.9
    return p


class Model(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = nemotron.Model(TINY)
        cls.expected = np.load(TINY / "expected.npy")

    def test_the_probabilities_are_the_ones_transformers_gives(self):
        got = self.model.probabilities(signal())
        self.assertEqual(got.shape, self.expected.shape)
        self.assertLess(np.abs(got - self.expected).max(), 1e-4)

    def test_the_speaker_cache_was_compressed_on_the_way(self):
        embeds, _, mask = self.model.embeddings(signal())
        self.assertFalse(mask[-1], "the last 80 ms frame holds only the frame past the audio")
        cache = nemotron.Cache(self.model)
        for start in range(0, len(embeds), self.model.chunk):
            end = min(start + self.model.chunk, len(embeds))
            x = np.concatenate([cache.frames(), embeds[start:min(end + self.model.lookahead, len(embeds))]])
            cache.update(x, self.model.network(x), end - start)
        self.assertTrue(cache.compressed)
        self.assertEqual(len(cache.embeds), self.model.cache_length)

    def test_a_folder_with_the_weights_is_a_nemotron_model_and_a_voiceprint_file_is_not(self):
        self.assertTrue(nemotron.is_model(TINY))
        self.assertFalse(nemotron.is_model(speakers.MODEL))

    def test_speakers_uses_it_for_a_nemotron_folder(self):
        timeline = speakers.diarize(signal(), None, 4, TINY)
        self.assertGreater(len(timeline.centers), 0)
        self.assertLessEqual(len(set(timeline.labels.tolist())), 4)
        self.assertEqual(timeline.summary()[1], [], "no voiceprints, so no similarities")


class ActivityTimeline(unittest.TestCase):
    def test_each_stretch_goes_to_the_speaker_heard_and_speakers_are_numbered_by_first_appearance(self):
        tl = speakers.activity_timeline(probs_of((2, 0, 200), (0, 200, 400), (2, 400, 480)))
        self.assertEqual([tl.speaker_at(t) for t in (0.5, 3.0, 4.5)], [1, 2, 1])
        self.assertEqual([round(c, 2) for c in tl.changes(0, 6)], [2.0, 4.0])
        talk, sims = tl.summary()
        self.assertEqual({s: round(v, 2) for s, v in talk.items()}, {1: 2.8, 2: 2.0})
        self.assertEqual(sims, [])
        self.assertEqual(tl.segments, [(0.0, 4.8)])

    def test_with_the_count_given_only_the_speakers_heard_longest_get_the_speech(self):
        p = probs_of((0, 0, 200), (1, 200, 400), (3, 400, 440))
        p[400:440, 1] = 0.3  # the third voice sounds a little like the second
        self.assertEqual(len(set(speakers.activity_timeline(p).labels.tolist())), 3)
        tl = speakers.activity_timeline(p, n_speakers=2)
        self.assertEqual(set(tl.labels.tolist()), {1, 2})
        self.assertEqual(tl.speaker_at(4.2), tl.speaker_at(3.0), "the third voice goes to the likelier of the two")

    def test_a_single_80_ms_flip_inside_a_turn_is_smoothed_away(self):
        tl = speakers.activity_timeline(probs_of((0, 0, 160), (1, 80, 88)))
        self.assertEqual(set(tl.labels.tolist()), {1})

    def test_pauses_split_the_speech_into_segments(self):
        tl = speakers.activity_timeline(probs_of((0, 0, 160), (1, 320, 480)))
        self.assertEqual(tl.segments, [(0.0, 1.6), (3.2, 4.8)])
        self.assertEqual(tl.speaker_at(2.0), 1, "a pause goes to the nearest speech")

    def test_a_word_goes_to_the_speaker_most_likely_over_the_whole_word(self):
        p = probs_of((0, 0, 300))
        p[100:104, 0], p[100:104, 2] = 0.6, 0.95  # a 40 ms interjection, too short for the 80 ms steps
        tl = speakers.activity_timeline(p)
        self.assertEqual(set(tl.labels.tolist()), {1})
        self.assertEqual(tl.speaker_at(1.02), 1)
        self.assertEqual(tl.speaker_over(1.0, 1.04), 2, "the interjection's word gets a speaker of its own")
        self.assertEqual(tl.speaker_over(2.0, 2.5), 1)

    def test_voiceprint_timelines_still_label_a_word_by_its_middle(self):
        tl = speakers.Timeline(np.array([0.5, 1.5, 2.5]), np.array([1, 2, 2]), np.zeros((3, 4)), [(0, 3)], 1.0)
        self.assertEqual(tl.speaker_over(0.0, 1.2), tl.speaker_at(0.6))

    def test_silence_gives_an_empty_timeline(self):
        tl = speakers.activity_timeline(probs_of())
        self.assertEqual(len(tl.centers), 0)
        self.assertIsNone(tl.speaker_at(1.0))


if __name__ == "__main__":
    unittest.main()
