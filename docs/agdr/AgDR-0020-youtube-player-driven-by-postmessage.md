---
id: AgDR-0020
timestamp: 2026-11-19T01:36:24Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Streamed YouTube videos play in YouTube's player, driven by postMessage

> In the context of YouTube videos that the user chose to stream from YouTube, so that only their sound is downloaded, facing a transcript page that has to play the video in step with its lines while the page loads nothing from the internet otherwise, I decided to embed YouTube's player from youtube-nocookie.com in an iframe only when the video is first played, and to drive it with the player's own postMessage messages through a small object that looks like the media element the page already follows, falling back to the downloaded sound when the player doesn't answer within about 8 seconds or reports an error, to achieve a page that keeps clicking a line and Follow playback as they are, contacts YouTube only when the user plays a streamed video, and loads no script from another site, accepting that the messages are YouTube's undocumented side of its IFrame API, which it could change.

## Context

- Issue [#77](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/77): a YouTube job downloaded the video and its
  sound and joined them into `video.mp4` to play beside the transcript. The speech models need only the sound, and a
  long course is many gigabytes of video.
- The owner chose a choice on the YouTube form, "Download the video" (as before) or "Stream it from YouTube", kept as
  a page preference, with "Download the video" until the user picks.
- The job page follows one media element: the bar's buttons, a line's time, the current line and Follow playback read
  its `currentTime` and `paused`, call `play()` and `pause()`, and listen to its `timeupdate`, `play`, `pause` and
  `loadedmetadata` events.
- The page loaded nothing from the internet, and the privacy policy said so. A streamed video can't play without
  reaching YouTube, so that becomes "nothing, except YouTube's player when a streamed video plays".
- YouTube's IFrame API is a script from `www.youtube.com` that creates the frame and talks to it with
  `postMessage`. The player in the frame answers a parent that says `listening` with `onReady`, sends its time and
  state as `infoDelivery`, reports `onError`, and does commands such as `playVideo`, `pauseVideo` and `seekTo`.
- YouTube refuses to play in a frame that sends no referrer, and an embedded player needs `enablejsapi=1` and the
  page's origin to talk to it.
- The page draws the job page again often, and the video panel is one element moved into each new drawing. Moving
  an iframe the usual way reloads it.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| An iframe of youtube-nocookie.com's embed, driven by postMessage from the page's own code, loaded when the video is first played | No script from another site runs in the page; the page reads only that frame's messages; a small object can look like a media element, so the sync code doesn't change; nothing reaches YouTube until the user plays | The message format is YouTube's own and isn't documented, so a change on its side could break it (then the page falls back to the sound) |
| Loading YouTube's IFrame API script into the page | YouTube's documented and supported way; it follows any change YouTube makes | A script from www.youtube.com runs in the page's own origin, beside the secret and the API; the page would load it, and contact YouTube, even before playing; it needs the script allowed wherever a Content-Security-Policy comes later |
| Downloading the video, as before | Works without internet; no third party in the page | The size and time this change is for: a 33-hour course is many gigabytes |

## Decision

Chosen: an iframe of youtube-nocookie.com's embed, driven by postMessage and loaded when the video first plays,
because it keeps every script in the page Sedjem's own, it reaches YouTube only when the user asks for the video,
and the page's existing sync code keeps working unchanged through an object that looks like a media element. The
owner chose the choice on the form and its preference; the agent chose the postMessage approach, loading the
player only when it first plays, and the frame policy.

- `POST /api/youtube` takes `stream`, and each job made from it, a playlist's lectures alike, keeps
  `"stream": true`. `app/youtube.py` then downloads only the sound: the best AAC stream, else the best in any codec,
  as `source.<ext>`, with no joining step and no note on the page, since it was chosen. Re-runs and branches keep
  `stream`.
- The page of a job with `stream`, a valid 11-character video ID and no `video.mp4` shows "Play from YouTube" in the
  video panel. The first play, from that button, the bar or a line, makes the iframe:
  `https://www.youtube-nocookie.com/embed/<id>?enablejsapi=1&playsinline=1&rel=0&origin=<the page's origin>`, with
  `referrerpolicy="strict-origin-when-cross-origin"`, a title, and
  `allow="autoplay; encrypted-media; picture-in-picture; fullscreen"`.
- `YouTubeMedia` in `app/static/youtube.js` sends `listening` until `onReady`, then commands, and reads
  `onReady`, `infoDelivery`, `onStateChange` and `onError`, only when the message's origin is
  `https://www.youtube-nocookie.com` and its source is that frame. It moves its time on with the clock between the
  player's reports, as YouTube's own API does, and leaves out a reported time from before a seek it asked for.
- No `onReady` within 8 seconds, or any `onError`, removes the frame. The page then says "YouTube's player couldn't
  load, so the sound plays from this computer" and plays the downloaded sound from the same moment, with its
  existing `?sig=` address. Its *Try again* loads the player once more, from where the sound was, and *Open on
  YouTube* opens the video's page in the browser.
- The page moves the video panel with `Element.moveBefore` where the browser has it, so drawing the page again
  doesn't reload the player; elsewhere the player loads again once and goes on from where it was. A player left on
  another page is closed before the panel goes in, so it doesn't load again for nothing.
- Like a media element given a new source, the player keeps the speed chosen in the bar and starts the next
  video's time at 0.
- The page is served with `Content-Security-Policy: frame-src https://www.youtube-nocookie.com`, so it can show no
  other site in a frame.

## Consequences

- A streamed course takes the space and download time of its sound alone.
- The privacy policy, the app notes and the threat model say that playing a streamed video loads YouTube's player
  and the video from Google's servers, which see the IP address and the video, and that YouTube may keep data in the
  browser. [ADR 2](../adr/0002-recordings-and-transcripts-stay-on-the-computer.md) says the interface loads nothing
  from the internet; that holds except for this player, which the user chooses for each link.
- Without internet, or for a video YouTube doesn't let other sites play, a streamed video plays its sound after
  about 8 seconds instead of its picture.
- If YouTube changes its messages, streamed videos fall back to their sound until the page follows the change.
- Jobs made before this change, and jobs that downloaded the video, play as before.
- The frame isn't sandboxed, since YouTube's player needs its scripts, its own origin and popups; Chromium-based
  browsers let a frame from another origin navigate the page only after a click in that frame.
