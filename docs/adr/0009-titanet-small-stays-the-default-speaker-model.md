---
status: Accepted
date: 2026-10-29
deciders: MohammedEl-sayedAhmed
---

# 9. Speaker labels by voice, with TitaNet-small as the default

## Context

Speakers have been told apart by their voices since the first version: a voiceprint every 0.75 seconds, then
spectral clustering, with NVIDIA's TitaNet-small from
[f54a405](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/f54a405)
([how it works](../04-speaker-labels.md#how-it-works)).
[Issue #17](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/17) asked for NVIDIA's Nemotron-3-Diarization to
be measured against the voiceprints, and [PR #18](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/18) scored
TitaNet-small, TitaNet-large, 3D-Speaker CAM++ and Nemotron on six excerpts of a private set of recorded meetings, on
ArzEn's Egyptian Arabic–English interviews and on AMI's English meetings ([conclusions](../04-speaker-labels.md#conclusions)).

## Decision

From [43c8c6f](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/43c8c6f)
([recommendation](../04-speaker-labels.md#recommendation)):

- TitaNet-small stays the default. It is a tenth of Nemotron's size (40 MB against 397 MB) and four to six times
  faster, its count of the people was right more often on the public sets, and on interviews and on meetings of two
  or three people Nemotron is only about level with it.
- Nemotron-3-Diarization can be chosen in Settings → Models, for meetings of four or more people and for recordings
  where people often talk over each other. It put the least on the wrong person on all three sets: 10.0% of the
  words on the excerpts against 10.2%, 2.2% of the speech on ArzEn against 2.5%, and 2.3% on AMI against 5.3%.
  Sedjem runs its weights with NumPy alone (`nemotron.py`).
- Until the chosen model is downloaded, TitaNet-small is used.

## Consequences

- Recordings of two or three people get labels at about the same accuracy for a fraction of the time and disk.
- Larger meetings need the user to choose Nemotron; with the default, a 5-person excerpt had 21.6% of its words on
  the wrong person against 19.4% with Nemotron.
- The overlap Nemotron hears is not used yet: every word still gets one speaker.
- The sets are small and were run on one laptop, Arabic is not among the languages of Nemotron's model card, and
  Nemotron was trained on AMI's other meetings, so the comparison may shift with more data.
