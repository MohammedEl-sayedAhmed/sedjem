# How Sedjem fits together

Three diagrams of Sedjem on one computer: what it talks to, the parts inside it, and the steps of one local
transcription. They are written in Mermaid, which GitHub shows as pictures. [The app](09-app.md) explains how to use
Sedjem, [the privacy policy](privacy.md) lists everything it contacts, and
[running jobs on another computer](15-remote-runs.md) covers the second server.

## What Sedjem talks to

You work in Sedjem's window. Each arrow gives the main reason for a connection, and the privacy policy gives the
details. An assistant reaches the transcripts through MCP, and a paired computer of yours can run jobs for this one.

```mermaid
flowchart LR
  you(["You"])
  assistant["AI assistant<br/>an MCP client, such as Claude, Cursor or VS Code"]
  sedjem["Sedjem<br/>transcripts with speaker labels,<br/>made on this computer"]
  other["Another computer with Sedjem<br/>paired with this one"]
  hosted["Hosted transcription services<br/>nine, each with your own API key"]
  models["Model hosts<br/>Hugging Face, GitHub releases<br/>and PyPI's file host"]
  github["GitHub<br/>releases and the tested main branch"]
  youtube["YouTube<br/>videos and captions from links"]
  aptabase["Aptabase<br/>anonymous usage counts"]

  you -->|"adds recordings,<br/>reads transcripts"| sedjem
  assistant -->|"reads transcripts<br/>(MCP over stdio)"| sedjem
  sedjem -->|"sends audio, gets the transcript<br/>(HTTPS, TLS 1.3)"| other
  sedjem -->|"uploads a recording<br/>after you allow it"| hosted
  sedjem -->|"downloads the models and<br/>libraries you choose"| models
  sedjem -->|"checks for updates if you agreed,<br/>downloads one when you ask"| github
  sedjem -->|"fetches a linked video"| youtube
  sedjem -->|"sends the counts<br/>when allowed"| aptabase

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef system fill:#1168bd,stroke:#0b4884,color:#fff
  classDef external fill:#8a8a8a,stroke:#5e5e5e,color:#fff
  class you person
  class sedjem system
  class assistant,other,hosted,models,github,youtube,aptabase external
```

## The parts inside Sedjem

Sedjem runs as one Python process: the local server, the job runner's threads, and the threads for model downloads,
updates and usage counts. Each local transcription gets a short-lived worker process, so the models never run in the
app itself, and cancelling a job ends that process. The MCP server is a separate process that the
assistant starts; it reads the data folder directly, so it works while the app is closed.

```mermaid
flowchart TB
  you(["You"])
  assistant["AI assistant<br/>an MCP client"]
  other["Another computer with Sedjem<br/>paired with this one"]
  internet["Internet services<br/>hosted services, model hosts,<br/>GitHub, YouTube and Aptabase"]

  subgraph sedjem["Sedjem"]
    window["Window<br/>pywebview, or a browser in app mode"]
    ui["Web UI<br/>HTML, CSS and JavaScript, no build step"]
    server["Local server<br/>Python http.server on 127.0.0.1:8765"]
    runner["Job runner<br/>six queues on Python threads"]
    worker["Model worker<br/>one subprocess per local job:<br/>faster-whisper, transcribe.cpp, sherpa-onnx"]
    remote["Remote server<br/>HTTPS on port 8766, off until switched on"]
    mcp["MCP server<br/>JSON-RPC over stdio, started by the assistant"]
    data[("Data folder<br/>jobs, settings, secrets.json,<br/>models and courses")]
  end

  you -->|"uses"| window
  window -->|"shows"| ui
  ui -->|"API calls and polling (HTTP)"| server
  server -->|"queues jobs"| runner
  runner -->|"starts and cancels"| worker
  worker -->|"progress and the lines"| data
  runner -->|"job.json and transcript.json"| data
  server -->|"reads and writes"| data
  server -->|"model downloads, update checks and downloads,<br/>YouTube link checks, usage counts (HTTPS)"| internet
  runner -->|"hosted uploads and YouTube<br/>(HTTPS)"| internet
  runner -->|"sends jobs (HTTPS)"| other
  other -->|"sends jobs (HTTPS)"| remote
  remote -->|"queues their jobs"| runner
  assistant -->|"asks (stdio)"| mcp
  mcp -->|"reads transcripts, writes its log"| data

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef part fill:#438dd5,stroke:#2e6295,color:#fff
  classDef external fill:#8a8a8a,stroke:#5e5e5e,color:#fff
  class you person
  class window,ui,server,runner,worker,remote,mcp,data part
  class assistant,other,internet external
```

## One local transcription, step by step

The page sends the recording, and the server answers at once. Threads then prepare the audio and run the worker,
while the page polls for progress; there is no WebSocket. Each step writes its state into the job's folder, so after a
restart, waiting jobs go back on the queue and a job that was running is marked as interrupted.

```mermaid
sequenceDiagram
  autonumber
  actor You
  participant UI as Web UI (app.js)
  participant Server as Local server (server.py)
  participant Prepare as Prepare thread
  participant Local as Local thread
  participant Worker as Model worker
  participant Data as Data folder

  You->>UI: Pick a recording and a model
  UI->>Server: POST /api/jobs with the file or its path, and the X-Sedjem header
  Server->>Server: Check the headers and that the model is ready
  Server->>Data: job.json (preparing), and the uploaded file if the page sent one
  Server->>Prepare: Queue the job
  Server-->>UI: 201 with the job summary
  Note over UI,Server: From here the page asks for GET /api/jobs/{id} every 1.2 s
  Prepare->>Data: audio.flac (16 kHz mono) and job.json (queued)
  Prepare->>Local: Queue the job
  Local->>Worker: Start python -m app.worker in its own process group
  loop Until the worker exits
    Worker->>Worker: Decode, load the engine, label speakers, transcribe
    Worker->>Data: progress.json and the lines so far
    Local->>Data: job.json with the progress, every 0.5 s
  end
  Worker->>Data: Word times and the run facts
  Worker-->>Local: Exits
  Local->>Data: transcript.json, job.json (done) and history version 0
  UI->>Server: GET /api/jobs/{id}
  Server-->>UI: Done, with the lines
  UI-->>You: Transcription finished, and the transcript
```

## Keeping the diagrams current

Change a diagram in the same pull request as a change that adds a part, a queue, a service or a step.
