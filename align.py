"""Word timestamps for a transcript by CTC forced alignment (ctc-forced-aligner + an MMS model).

Cohere Transcribe and the llama models return text without timing. Aligning that text to the audio
gives every word a start and an end, so speaker labels can be attached word by word, as with
Whisper, instead of cutting the audio where the speaker changes (which costs the model its context).

The default model, MahmoudAshraf/mms-300m-1130-forced-aligner (1,130 languages), reads romanized
text: Arabic script and the English words mixed in are both romanized with uroman first. Its
licence is CC-BY-NC-4.0 (non-commercial use only).

Needs: pip install torch --index-url https://download.pytorch.org/whl/cpu
       pip install transformers uroman "ctc-forced-aligner @ git+https://github.com/MahmoudAshraf97/ctc-forced-aligner"
"""
import numpy as np


class Aligner:
    def __init__(self, model_dir, threads=4, language="ara"):
        import torch
        from ctc_forced_aligner import load_alignment_model

        torch.set_num_threads(threads)
        self.torch = torch
        self.model, self.tokenizer = load_alignment_model("cpu", model_path=str(model_dir), dtype=torch.float32)
        self.name = f"aligned-{str(model_dir).rstrip('/').rsplit('/', 1)[-1]}"
        self.language = language
        self.failures = 0

    def words(self, audio, text):
        """[(start, end, word)] in seconds from the start of `audio`. If the alignment fails (e.g. far
        more text than audio), the words are spread evenly over the audio instead."""
        from ctc_forced_aligner import generate_emissions, get_alignments, get_spans, postprocess_results, preprocess_text

        if not text.split():
            return []
        waveform = self.torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))
        try:
            emissions, stride = generate_emissions(self.model, waveform, batch_size=1)
            tokens, text_starred = preprocess_text(text, romanize=True, language=self.language)
            segments, scores, blank = get_alignments(emissions, tokens, self.tokenizer)
            spans = get_spans(tokens, segments, blank)
            out = [(float(r["start"]), float(r["end"]), r["text"])
                   for r in postprocess_results(text_starred, spans, stride, scores) if r["text"].strip()]
            if out:
                return out
        except Exception:  # noqa: BLE001 — fall back to even spacing below
            pass
        self.failures += 1
        return self.spread(text, len(audio) / 16000)

    @staticmethod
    def spread(text, seconds):
        """The words spread evenly over `seconds` (when their exact times aren't needed or known)."""
        words = (text or "").split()
        return [(i * seconds / len(words), (i + 1) * seconds / len(words), w) for i, w in enumerate(words)]
