# Threat model

This page weighs, for each part of Sedjem that holds secrets or talks to other programs, what could go wrong, what
already stops it, and what is left. It was made on 18 November 2026 from the code at 2585786, with STRIDE (spoofing,
tampering, repudiation, information disclosure, denial of service, elevation of privilege) applied to each place where
data crosses from one side to another. [How Sedjem fits together](16-architecture.md) shows the parts.

Three changes would make Sedjem harder to attack, and each has an issue:
[#125](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/125) (the page and the local server),
[#126](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/126) (code from outside) and
[#127](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/127) (file permissions and one parser). A security
problem that this page misses goes privately to the maintainer, as [the security policy](../SECURITY.md) says.

## The local server and its page

The app's page and its API are served by a local server, which asks for no password.

- Another website open in the same browser could try to use the API (cross-site requests, or DNS rebinding). The
  server listens on this computer only and doesn't start on another host. It answers only requests addressed to
  127.0.0.1, `localhost` or `[::1]`, refuses another site's Origin, and needs the `X-Sedjem: 1` header, which another
  site can't send, on every request that changes something.
- Another program on the computer, including one that another user account runs, could connect to the server. Every
  request for data needs a secret that only the user's own window has. It is kept in a file only the user can read,
  and it never appears in a command line or an address the app opens. Before a second start trusts a copy that is
  already running on a port, that copy proves that it knows the secret
  ([AgDR 19](agdr/AgDR-0019-a-secret-between-the-server-and-its-window.md)).
- A program could take the app's port while Sedjem isn't running, and leave a page or a service worker for that
  address in the browser, which would see the next login. The port and the secret are new at each start, so such a
  program can't know where Sedjem will be; the app opens its page with a browser profile of its own when it finds a
  Chromium-family browser, and offers no installed web app. A port fixed with `--port` or `[server] port` gives that
  up. On Windows the port is opened for the app alone, so no other account can bind it too.
- A request to transcribe a file by its path, and `POST /api/probe`, read any file the user can read. That is what
  they are for, and only the user's own window can send them.
- Text from outside (YouTube titles and captions, the hosted services' transcripts, paired computers, Hugging Face,
  assistants, release notes) could carry script into the page. Every such text is escaped or set as plain text, and
  links built from data are `https://` only. A one-off review of every place the page builds HTML, with injected
  script tried in eleven views, found none that runs; the repository has no test for this yet.
- A video added with streaming plays in YouTube's player, a frame from `www.youtube-nocookie.com` that the page loads
  only when the video is played ([AgDR 20](agdr/AgDR-0020-youtube-player-driven-by-postmessage.md)). The frame's
  address is built from a video ID that the page checks first, the page reads only messages from that frame with
  YouTube's origin, and its Content-Security-Policy allows no other frame. The frame is another origin, so it can't
  read the page, its secret or the API.
- Left: requests that only read (GET) need no header, so another site could play a recording in a media element if it
  guessed the job's ID; it can't read the bytes. The page's Content-Security-Policy limits only frames, so it is not
  yet a second layer against script. Both are in [#125](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/125).

## The model worker

Each local transcription runs in a worker process that the app starts with the recording's path and the model's
settings, and that writes its progress and output into the job's folder. It reads only local files: models that
were checked when they were downloaded, or models already in the Hugging Face cache.

## The hosted services

- A recording goes to a hosted service only after the user allows that upload, over HTTPS, with the service's API key.
  Sedjem deletes the uploaded file or the transcript afterwards where the service's API can (ElevenLabs, Speechmatics,
  Google Gemini, AssemblyAI). Each service's own terms say what it keeps ([privacy policy](privacy.md)).
- The API keys come from environment variables or from `secrets.json` in the data folder, which only its owner can
  read on Linux and macOS. On Windows only the profile folder's permissions protect it; checking that is part of
  [#127](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/127).
- The data folder's `config.toml` can change where a service is reached, but only someone who can already write the
  user's files can change it.

## Running jobs on another computer

[Remote runs](15-remote-runs.md) are off until *Run jobs for other computers* is switched on.

- Spoofing a computer: pairing needs an 8-character code that lasts 10 minutes and allows 5 tries, and both sides
  prove they know it without sending it. After pairing, the sender pins the other computer's certificate (TLS 1.3) and
  each request carries a token, which the sender keeps in `secrets.json` and the receiver only as a hash.
- The listener answers only private network addresses. A shared network, such as public Wi-Fi, also uses private
  addresses, so pairing and the token are what keep strangers out there. An address with 10 failed tries in 10
  minutes is blocked.
- Too many requests: 32 connections at a time, 4 from one address, time-outs, and 10 jobs and 6 GB for each paired
  computer.
- What comes back from the other computer is treated as data and checked before it is shown.
- Left: the list of paired computers, with their pinned certificates, and the settings file are written with the
  default file permissions; [#127](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/127) makes them private
  like the keys.

## Assistants over MCP

An assistant starts the [MCP server](11-assistants.md) itself and talks to it over standard input and output, so
only programs the user runs can reach it.

- An assistant sees only what the visibility switches allow, and its provider sees what it reads.
- A transcript could hold words that try to steer the assistant. Assistants may change transcripts only when that is
  switched on (it is off at first), each change is a new version that can be restored, and every tool call, read
  and prompt is logged.
  The server tells the assistant that transcript text is quoted data, not instructions.

## YouTube links

- Only links to YouTube's own addresses are accepted, only YouTube's extractors run, and media and captions are taken
  only from YouTube's hosts.
- yt-dlp runs a script from YouTube to read the video's address. It runs in Deno without permissions or in Node.js
  with its permission model. Left: without either, it can run in QuickJS, which has no sandbox; and a Whisper model
  added from Hugging Face can be converted from a `pytorch_model.bin`, which can hold code. Both are in
  [#126](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/126).

## Updates and models

- An update is downloaded over HTTPS, only from this repository's release files, and checked against the
  `SHA256SUMS.txt` of the same release before it is installed. A setup from source updates only by a fast-forward of
  main.
- Left: the checksums come from the same place as the installer, and the installer isn't signed, so someone who took
  over the repository's releases could replace both ([signing](14-pipeline.md#signing)).
- Each built-in model file is pinned by its revision and its SHA-256. A model added from Hugging Face is checked
  against the hashes that Hugging Face itself lists for it.

## Usage counts

[The usage counts](13-usage-counts.md) are anonymous and can be turned off. The service that receives them,
Aptabase, still sees the address they come from and the operating system and its language.

## Who changed what

Each saved version of a transcript records whether the user or an assistant made it, and the MCP log records what
assistants asked for.
