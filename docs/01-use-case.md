# Use case

The recordings are technical meetings and phone calls, from two minutes to several hours long.
People speak mostly Egyptian Arabic, with English tech terms and whole English sentences mixed in,
which is called code-switching. A typical sentence from the public Perle test set:
`الـ task الجديدة دي محتاجة نعمل integrate مع payment gateway`.

The transcript needs timestamps and a label for who is speaking (Speaker 1, Speaker 2 and so on),
with speakers told apart by their voices rather than by time slices. This was for personal trials,
not a commercial product, so it had to be free to try. The recordings are private meetings, so no
audio was to be uploaded to any outside service without explicit permission. Everything in this
project ran on the test laptop.

## The test audio

None of the private audio below, and nothing transcribed from it, is in this repository.

Four phone calls, saved as 8 kHz mono 16-bit WAV (phone quality):

| Recording | Length | Notes |
|---|---|---|
| rec1, the 2-minute test call | 2:00 | 2 speakers, a technical discussion; used in every early run |
| rec2 | 16:00 | 2 speakers; used for the full-length runs |
| rec3 | 25:42 | |
| rec4 | 79:37 | |

A file sampled at 8 kHz holds nothing above 4 kHz, which makes every model's job harder than with
normal 16 kHz audio. The cost is measured in [the results](03-results.md#phone-audio).

Six real work meetings with reference transcripts, built into a separate private set that stays
outside the repository: 9 h 51 min in total, 2 to 9 participants, recorded at 16 kHz by the meeting
platform or a local recorder. Each comes with an ElevenLabs transcript that was checked to be
verbatim (English kept in Latin script), with the speakers matched to the real people by hand. With
these, both the text and the speaker labels can be scored on real meetings
([results](03-results.md#real-meetings)).

## The original question

The project started from a handoff note ([kept here](research/r2t2-handoff.md)). It asked whether
R2T2 (Confucius4-R2T2 from NetEase Youdao) could do this instead of ElevenLabs Scribe, since there
was no ElevenLabs key. An earlier desk review had judged it a poor fit: trained only on Chinese and
English, built for live streaming, no speaker labels, and apparently in need of a GPU. The note
asked for a hands-on comparison of R2T2, its base model Qwen3-ASR-1.7B and Whisper large-v3 on
Arabic–English audio.

That comparison was run, and then the work went further: a search for the best free option overall,
and a working transcriber with speaker labels, which became Sedjem.
