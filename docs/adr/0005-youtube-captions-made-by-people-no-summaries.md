---
status: Accepted
date: 2026-10-19
deciders: MohammedEl-sayedAhmed
---

# 5. YouTube: captions made by people, and no summaries inside the app

## Context

[Issue #1](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/1) asked for transcripts of YouTube videos and
playlists, with the video playing beside the text and a playlist kept as a course. YouTube has two kinds of
captions: those written by people, and automatic ones made by its own speech recognition. The issue counts only
the first as good captions, and asks for each course to be saved as files ready to paste into an assistant for
study notes.

## Decision

From [e896baa](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/e896baa)
([the app, YouTube](../09-app.md#youtube-videos-and-courses)):

- When a video has captions made by people in its own language, Arabic or English, they become its transcript and
  no model runs. YouTube's automatic captions are never used. Captions in another language are a translation, so
  that video goes to the chosen model, as does every video without captions made by people. Using captions can be
  turned off for a link.
- A video is downloaded up to 720p (H.264 and AAC, joined by PyAV without re-encoding), so it plays beside its
  transcript.
- A playlist becomes a course. Each finished lecture is written to the `courses` folder as Markdown and SRT, and
  `course.md` holds the whole course as plain text.
- The app doesn't summarise. Summaries, study notes and quizzes are left to an assistant, from the course files or,
  later, over MCP ([ADR 6](0006-assistants-over-mcp-local-and-read-only.md)).
- Downloading is for personal use, and keeping to YouTube's terms is up to the user.

## Consequences

- A lecture with captions made by people is ready at once and has the text its makers wrote, with one speaker.
- A video with only automatic captions takes a model run, as long as for a recording of the same length.
- Sedjem holds no summarising model and sends no transcript anywhere to be summarised; the user chooses the
  assistant.
- Fetching from YouTube needs yt-dlp and, for some formats, a JavaScript runtime, which the app can download
  ([the JavaScript runtime](../09-app.md#the-javascript-runtime)).
