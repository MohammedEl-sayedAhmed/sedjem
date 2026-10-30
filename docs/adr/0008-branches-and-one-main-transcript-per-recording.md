---
status: Accepted
date: 2026-10-29
deciders: MohammedEl-sayedAhmed
---

# 8. Branches, and one main transcript per recording

## Context

A recording could already have several transcripts, one per model run (*Run again with*), each with a version
history, and stretches of them could be compared and combined. Nothing marked one of them as the transcript to
curate. Assistants were also to save corrections ([issue #10](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/10),
[ADR 6](0006-assistants-over-mcp-local-and-read-only.md)), so the app and another process could save versions of
the same transcript at the same time.

## Decision

From [PR #19](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/19), starting with
[bd12cbb](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/bd12cbb)
([main transcript and branches](../09-app.md#main-transcript-and-branches)):

- A recording works like a repository. The transcripts the models made are its branches, and a new branch can be
  made from any version of any of them, as a copy that shares the audio and runs no model.
- A recording has at most one main transcript, chosen by the user from its finished transcripts. It is listed
  first, opens from the recording's title, is the base of a comparison, and is the one a lecture's course files
  and the assistants' search follow. A branch can hold unchecked text while the main transcript holds checked text.
- A save names the version it was made on (`base_version`). When that is no longer the newest, the save is refused
  with 409 and nothing changes, and every change to a history holds a lock in the transcription's folder.
- Assistants can save a corrected version with `save_version`, which is off until the user turns it on in
  Settings → Assistants ([3b46cb1](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/3b46cb1)). Transcript
  text is quoted data that anyone in a recording could have said, so an instruction in it must not be able to lead
  to a save. While saving is off, the tool isn't offered and a call to it is refused
  ([saving a version](../11-assistants.md#saving-a-version)).

## Consequences

- One transcript per recording can be curated, without losing the models' own output or the other branches.
- Two writers can't overwrite each other's work: the later one is told the version moved on.
- Nothing an assistant does deletes anything; every earlier version is kept and History can restore it.
- A recording without a main transcript works as before, so older data needs no change.
- Every place that picks a transcript of a recording (the sidebar, compare, course files, search, assistants) has
  to respect the main mark.
