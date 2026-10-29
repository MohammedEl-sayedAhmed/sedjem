# Privacy policy

Sedjem is a transcription app that runs on your own computer. This policy says what it sends over the internet, to
whom and when, and what stays on the computer. It covers the desktop app, its Windows installer and Sedjem run from
the source code.

## No account

Sedjem has no account and no sign-in of its own. The hosted services you may choose are used with your own API
keys, under your own accounts with them.

## What stays on the computer

Recordings, transcripts and their versions, speaker names, API keys, settings and logs stay in Sedjem's data folder
on the computer: `%LOCALAPPDATA%\Sedjem` on Windows, `~/.local/share/sedjem` for the Linux build, and the project
folder when Sedjem runs from source ([where things are kept](10-desktop.md#where-things-are-kept)). The models it
downloads are kept there too. Its server answers only on the computer itself (127.0.0.1), and its interface loads
nothing from the internet. Uninstalling keeps the data folder; deleting the folder removes everything.

## What Sedjem contacts, and when

Sedjem goes online only for the following, and only when the reason given applies. Each of these services sees the
computer's IP address, as with any connection, and handles what it receives under its own privacy policy, linked
here.

- Hugging Face (`huggingface.co`, [privacy policy](https://huggingface.co/privacy)), when you download a model in
  *Settings → Models*: the speech models, the Whisper tokenizer and NVIDIA's Nemotron-3-Diarization. Adding a model
  from a Hugging Face link asks it about that model.
- GitHub ([privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)):
  `github.com` for the other speaker models, from the releases of the sherpa-onnx project, and for Sedjem's own
  releases when you update; `api.github.com` to check for updates, once a day if you allowed it when Sedjem first
  asked, or when you click *Check now*. Some checks tell GitHub which version is running
  ([updates](12-updates.md#what-is-sent-to-github)). The self-test with `--models` also fetches a public speech
  sample from GitHub.
- PyPI's file host (`files.pythonhosted.org`), run by the Python Software Foundation
  ([privacy notice](https://policies.python.org/pypi.org/Privacy-Notice/)), only for what you download in Settings:
  NVIDIA's cuBLAS and NVRTC libraries, which Whisper uses on an NVIDIA graphics card, and Deno, which YouTube links
  need.
- YouTube, which is Google's ([privacy policy](https://policies.google.com/privacy)), with its video hosts
  (`*.googlevideo.com`), when you add a link: Sedjem asks, through yt-dlp, for that video or playlist, its streams and
  its captions, and downloads them.
- Aptabase ([privacy policy](https://aptabase.com/legal/privacy)), for anonymous usage counts, at `eu.aptabase.com`
  (a copy built with a key from Aptabase's US region uses `us.aptabase.com`). A few times a month Sedjem sends
  anonymous counts, so that the project knows how many installs use it and what they use: the version, the system
  and the computer in broad terms, some settings, which models are downloaded, and how many transcriptions, exports
  and other actions there were, with which models. It never sends recordings, transcripts, titles, speaker names,
  file names, paths, links or keys, and no identifier.
  By its terms, Aptabase uses the IP address only in memory, to find the country and region and to make a
  pseudonymous hash that changes every day, and doesn't store it. [Usage counts](13-usage-counts.md) lists every
  event and value, and how the public totals are made from them.
- The hosted transcription service you choose, with your own API key. A recording goes to one only when you pick
  it and tick the box that allows the upload, each time, and that provider's own privacy policy applies to it.
  [The app notes](09-app.md#the-models-and-their-settings) say what each one keeps and whether it trains on the
  audio.

| Hosted service | Privacy policy |
|---|---|
| ElevenLabs | [elevenlabs.io/privacy-policy](https://elevenlabs.io/privacy-policy) |
| Speechmatics | [speechmatics.com/legal/privacy-policy](https://www.speechmatics.com/legal/privacy-policy) |
| Google Gemini | [policies.google.com/privacy](https://policies.google.com/privacy) |
| Deepgram | [deepgram.com/privacy](https://deepgram.com/privacy) |
| AssemblyAI | [assemblyai.com/legal/privacy-policy](https://www.assemblyai.com/legal/privacy-policy) |
| Azure AI Speech | [Microsoft's privacy statement](https://www.microsoft.com/privacy/privacystatement) |
| OpenAI | [openai.com/policies/row-privacy-policy](https://openai.com/policies/row-privacy-policy/) |
| Groq | [groq.com/privacy-policy](https://groq.com/privacy-policy) |
| Mistral | [legal.mistral.ai/terms/privacy-policy](https://legal.mistral.ai/terms/privacy-policy/) |

## Turning the usage counts off

The counts are on by default, and nothing is sent before Sedjem has said so, on its first start or on the Privacy
page of the Windows installer. Any of these turns them off:

- unticking *Send anonymous usage counts* on the installer's Privacy page, or installing silently with
  `/USAGECOUNTS=0`
- the switch *Send anonymous usage counts* in *Settings → About*, at any time
- `DO_NOT_TRACK=1` or `SEDJEM_NO_USAGE=1` in the environment Sedjem starts from

Once they are off, nothing is counted, and the counts not sent yet are deleted.

## Assistants

An assistant connected over MCP, such as Claude Desktop, Cursor or VS Code, reads the transcripts that
*Settings → Assistants* allows, on the computer. What it reads then goes to the assistant's own model and service,
under that service's terms. It never gets the audio, the keys or the settings ([assistants](11-assistants.md)).

## Changes and contact

This policy is kept in the repository with the code, and its history shows every change. Questions about privacy
can be asked in [the project's issues](https://github.com/MohammedEl-sayedAhmed/sedjem/issues).
