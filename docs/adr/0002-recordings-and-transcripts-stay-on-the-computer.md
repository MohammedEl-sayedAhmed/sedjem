---
status: Accepted
date: 2026-09-25
deciders: MohammedEl-sayedAhmed
---

# 2. Recordings and transcripts stay on the computer

## Context

The recordings are private meetings and calls, mostly in Egyptian Arabic mixed with English. The project started
with three requirements ([use case](../01-use-case.md)): a transcript with speakers told apart by voice, free to
try, and no audio uploaded to any outside service without explicit permission. The first commit,
[1f0f98e](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/1f0f98e), was a local transcriber built on that
basis.

## Decision

Transcription and speaker labels run on the computer, with open models, by default. Hosted services can be used,
but only as a choice for one recording at a time:

- Recordings, transcripts, their versions, speaker names, API keys, settings and logs are kept in Sedjem's data
  folder on the computer, and the models it downloads are kept there too.
- The app's server listens on 127.0.0.1 only and refuses requests addressed to any other host, and its interface
  loads nothing from the internet.
- A hosted model uploads a recording only after the user picks it and ticks "Upload this recording to ...", and
  the server checks that again, re-runs included. Each service is used with the user's own key, and a key goes
  only to its own service.
- Every service Sedjem contacts, and when, is listed in the [privacy policy](../privacy.md).

## Consequences

- With a local model, nothing about a recording leaves the computer, and Sedjem works offline once its models are
  downloaded.
- The local models need disk space ([disk and cleanup](../08-disk-and-cleanup.md)) and run at about a quarter to
  half of real time on a laptop processor.
- On real meetings the local models are well behind a hosted service: their text differs from ElevenLabs' on
  44–49% of the words ([results](../03-results.md#real-meetings)). The [recommendation](../07-recommendation.md)
  says so, and suggests a hosted service for minutes that people rely on.
- Every later feature that goes online had to be added to the privacy policy and asked for or announced first:
  model downloads, YouTube links ([ADR 5](0005-youtube-captions-made-by-people-no-summaries.md)), the update check
  ([ADR 7](0007-updates-asked-first-two-channels-tested-builds.md)) and the usage counts
  ([ADR 10](0010-anonymous-usage-counts-through-aptabase.md)).
