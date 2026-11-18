"""The Egyptian style hint for Whisper, and which models get it by default.

transcribe.py and the app both read these. This module imports no speech engine, so the app can build a job's
command, or a hosted model's prompt, without loading faster-whisper or sherpa-onnx into its own process.
"""
import re

# An Egyptian sentence with English terms in Latin script. As Whisper large-v3's initial prompt it
# keeps Whisper in dialect (instead of rewriting into formal Arabic) and writes English terms in
# English: WER 47% -> 34% on ArzEn, 40% -> 22% on Perle. The code-switching fine-tune doesn't need it.
STYLE_PROMPT = "يعني احنا كنا بنتكلم عن ال project بتاعنا و ال deadline و ال meeting اللي جاي مع ال team."


def gets_style_hint(name):
    """Whether a Whisper model gets STYLE_PROMPT by default: large-v3, where it was measured (39.7% to 20.8% WER on
    Perle), in faster-whisper form ("…/large-v3") or as a GGUF file ("whisper-large-v3-Q8_0.gguf"); not turbo."""
    return re.search(r"large-v3(?![-_.]?turbo)", str(name).lower()) is not None
