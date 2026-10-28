"""bench/eval_meetings.py tells, word by word, which words went to the wrong person, so that
bench/compare_nemotron.py can score the words said while two people talk at once on their own."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
from eval_meetings import evaluate  # noqa: E402

TURNS = [("Amal", "one two three"), ("Badr", "four five six")]


class RightSpeaker(unittest.TestCase):
    def test_each_aligned_word_says_whether_it_went_to_the_right_person(self):
        hyp = [("one", "spk1"), ("two", "spk1"), ("three", "spk2"), ("four", "spk2"), ("five", "spk2"),
               ("six", "spk2")]
        r = evaluate(TURNS, hyp)
        self.assertEqual(r["right_speaker"], {0: True, 1: True, 2: False, 3: True, 4: True, 5: True})
        self.assertAlmostEqual(r["wder"], 1 / 6)

    def test_a_word_missing_from_the_reference_has_no_verdict(self):
        hyp = [("one", "spk1"), ("two", "spk1"), ("three", "spk1"), ("four", "spk2"), ("five", "spk2"),
               ("six", "spk2"), ("seven", "spk2")]
        r = evaluate(TURNS, hyp)
        self.assertEqual(sorted(r["right_speaker"]), [0, 1, 2, 3, 4, 5])
        self.assertTrue(all(r["right_speaker"].values()))
        self.assertEqual(r["wder"], 0)


if __name__ == "__main__":
    unittest.main()
