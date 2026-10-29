# Usage counts

Sedjem sends anonymous counts to Aptabase, so that the project knows how many installs use it and in which
countries, which versions, systems and settings they run, and what they use: which models, local or hosted, speaker
labels, exports, YouTube, editing and assistants. The counts are on by default and can be turned off at any time.
Nothing is counted or sent before Sedjem has said so: on first start, the dialog that asks about updates also says
that the counts are on and has their switch, and a copy updated from a version without them shows the same notice
once.

This page lists everything that is sent and when, what is never sent, where it goes, how to turn the counts off,
and how the public totals are made from them. Every value is a whole number or one of a few fixed values.

## When something is sent

| Event | When | What it adds |
|---|---|---|
| `install` | once for each data folder, the first time the counts are allowed | nothing |
| `active_month` | once a calendar month, the first time Sedjem runs that month | the month, how long ago the install was counted, the kind of install and the window, some settings, the computer in broad ranges, the size of the library, and which models are downloaded and which hosted services have a key |
| `week_summary` | once for each week in which Sedjem was used, the first time it runs after that week ended | the week, on how many days Sedjem was used and how often it was started, and how many runs, exports, edits and other actions there were |

Months and weeks are counted in UTC, and weeks are ISO weeks, Monday to Sunday. Each `active_month` names its month
and each `week_summary` its week, and an event gets its time when it is sent, so one that waits while the computer
is offline still counts for the right month. An install used every week sends about six events a month. Sedjem
looks about twenty seconds after it starts and then every fifteen minutes while it runs, so a month or a week that
begins while it is open is counted too.

## What each event holds

Every event has its name, the time it was sent (UTC, to the millisecond), a session number and these details of
the system:

| Field | Value |
|---|---|
| `osName` | `Windows`, `Linux` or `macOS` |
| `osVersion` | on Windows the version number (`10.0.26100`), on Linux the name in `/etc/os-release` (`Ubuntu 24.04.4 LTS`), on macOS its version |
| `locale` | the system's language and region, such as `ar-EG` or `en-US` |
| `appVersion` | the version *Settings → About* shows, such as `0.3.1` (a nightly preview's is like `0.3.2-preview.20261101`) |
| `engineName`, `engineVersion` | `python` and the version of Python, such as `3.12.15` |
| `sdkVersion` | `sedjem-usage@1` |
| `isDebug` | `false`, or `true` when `SEDJEM_USAGE_DEBUG=1` is set, for trying the counts out (Aptabase keeps those apart from the others) |

The session number is the second the events were sent followed by eight random digits. A new one is drawn for each
request, so it ties together only the events sent together.

`active_month` adds these. The ones marked `0`, `1` are numbers, the others words:

| Prop | Values | Meaning |
|---|---|---|
| `month` | such as `2026-11` | the month counted |
| `install_age` | `0`, `1`, `2`, `3-5`, `6-11`, `12+` | calendar months since the install was counted, `0` in that month |
| `kind` | `installer`, `linux-build`, `source`, `other` | installed with the Windows installer, the Linux build, run from the source code, or another desktop build |
| `ui` | `desktop`, `browser` | started as the desktop app (`Sedjem`, `python -m app.desktop`, `./app.sh --desktop`), or with `./app.sh` in a browser tab |
| `channel` | `stable`, `latest` | the channel in *Settings → Updates* |
| `update_check` | `on`, `off`, `unasked` | whether Sedjem checks for updates by itself, or the first-start question wasn't answered |
| `language` | `ar`, `en`, `auto`, `other` | the language a new transcription starts with (`language` under `[defaults]` in the config); `other` for one the app doesn't know |
| `speaker_model` | `titanet_small`, `titanet_large`, `campp`, `nemotron`, `other` | the speaker model chosen in *Settings → Models*: TitaNet-small, TitaNet-large, 3D-Speaker CAM++, Nemotron-3-Diarization, or one added to the config |
| `device` | `auto`, `cpu` | whether the graphics card may be used (*Settings → Speed*) |
| `mcp_write` | `on`, `off` | whether assistants may save versions (*Settings → Assistants*) |
| `cpu` | `intel`, `amd`, `arm`, `other` | the processor's maker, `arm` for any ARM processor |
| `gpu` | `nvidia`, `amd`, `intel`, `other`, `none` | the maker of the graphics card that Sedjem's own check found and can use, NVIDIA first, then AMD, then Intel when there are several; `none` when it found none or couldn't look |
| `gpu_vram` | `none`, `under 4`, `4-8`, `8-16`, `over 16` | the memory of the best dedicated graphics card in GB: a card with 4, 6 or 8 GB counts as `4-8`, one with 12 or 16 GB as `8-16`; `none` without a dedicated card, since integrated graphics share the computer's memory |
| `cuda` | `yes`, `no` | whether an NVIDIA card can be used through CUDA |
| `vulkan` | `yes`, `no` | whether a graphics card can be used through Vulkan |
| `ram` | `under 8`, `8-16`, `16-32`, `over 32` | the memory in GB: a computer with 8 or 16 GB counts as `8-16`, one with 24 or 32 GB as `16-32` |
| `cores` | `under 4`, `4-8`, `8-16`, `over 16` | the processor's threads: 4 to 8 count as `4-8`, 9 to 16 as `8-16` |
| `power` | `power-saver`, `balanced`, `performance`, `unknown` | the power profile on Linux; `unknown` where Sedjem can't read it (Windows, macOS, a Linux without `powerprofilesctl`) |
| `disk_free` | `under 10`, `10-50`, `50-200`, `over 200` | the free space in GB on the disk of the data folder: 10 up to 50 counts as `10-50` |
| `library` | `0`, `1-9`, `10-49`, `50-199`, `200+` | how many transcriptions there are |
| `courses` | `0`, `1-4`, `5-19`, `20+` | how many courses, YouTube playlists added as courses, there are |
| `has_cohere`, `has_whisper_medium`, `has_whisper_large`, `has_other_local` | `0`, `1` | `1` when the model is downloaded: Cohere Transcribe Arabic, whisper-medium code-switching, Whisper large-v3, or any other local model |
| `has_titanet_large`, `has_campp`, `has_nemotron` | `0`, `1` | `1` when the speaker model is downloaded |
| `key_elevenlabs`, `key_speechmatics`, `key_gemini`, `key_deepgram`, `key_assemblyai`, `key_azure`, `key_openai`, `key_groq`, `key_mistral` | `0`, `1` | `1` when the hosted service has a key, saved in the app or set in the environment: whether there is one, never the key |

`week_summary` adds these whole numbers, counted on the computer during that week:

| Prop | What it counts |
|---|---|
| `week` | the week counted, such as `2026-W44` |
| `transcriptions` | model runs that finished, local or hosted (taking a YouTube video's captions made by people is not a model run) |
| `minutes` | the length of their audio, added up and rounded to whole minutes |
| `cohere`, `whisper_medium`, `whisper_large`, `other_local`, `hosted` | the same runs by model: Cohere Transcribe Arabic, whisper-medium code-switching, Whisper large-v3, any other local model (one added from Hugging Face, say), and any hosted service |
| `elevenlabs`, `speechmatics`, `gemini`, `deepgram`, `assemblyai`, `azure`, `openai`, `groq`, `mistral` | the hosted runs by service |
| `speakers` | runs whose transcript has speaker labels |
| `speakers_given`, `speakers_estimated` | of those, the runs that were given the number of speakers, and the ones that estimated it |
| `titanet_small`, `titanet_large`, `campp`, `nemotron` | runs whose labels came from that speaker model on this computer (a hosted service that labels the speakers itself counts in none of them) |
| `runs_under_5`, `runs_5_30`, `runs_30_60`, `runs_over_60` | the runs by the length of their audio in minutes: under 5, 5 up to 30, 30 up to 60, and 60 or more |
| `lang_ar`, `lang_en`, `lang_auto` | the runs by the language chosen for them |
| `cpu_runs`, `gpu_runs` | local runs that finished on the processor, or on a graphics card |
| `gpu_fallbacks` | local runs that went on on the processor after an error on the graphics card (they count in `cpu_runs` too) |
| `min_cohere`, `min_whisper_medium`, `min_whisper_large`, `min_other_local`, `min_hosted` | the audio of the runs by model, in whole minutes |
| `proc_cohere`, `proc_whisper_medium`, `proc_whisper_large`, `proc_other_local`, `proc_hosted` | the time those runs took by model, in whole minutes, which with the audio gives each model's speed |
| `failed` | jobs that failed |
| `failed_read`, `failed_model`, `failed_hosted`, `failed_download` | the same by where they stopped: a recording that couldn't be read, a local model, a hosted service, a YouTube download |
| `cancelled` | jobs cancelled while they waited or ran, or deleted while they ran |
| `youtube` | YouTube videos transcribed, by a model or from their captions |
| `youtube_captions` | of those, the ones that took the captions made by people |
| `courses_created` | YouTube playlists added for the first time |
| `compares` | times the compare view was opened |
| `combines` | versions saved from the compare view |
| `edit_sessions`, `renames`, `merges` | versions saved by editing lines, changes of the title or of a speaker's name, and speakers merged |
| `branches`, `restores` | branches made, and versions restored |
| `txt`, `srt`, `vtt`, `md`, `json` | exports in each format, downloaded or saved |
| `copies` | times the text was copied with *Copy text* |
| `days_used` | on how many days of the week Sedjem was started or anything above was counted, by the computer's own calendar |
| `starts` | times Sedjem was started |
| `assistant_requests`, `assistant_saves` | how many requests assistants made over MCP, and how many versions they saved, counted from the entries of the log in *Settings → Assistants* (never what they asked) |
| `model_downloads` | models and speaker models downloaded (not NVIDIA's libraries or Deno) |
| `update_installs` | times *Update now* was started |

These are the only places where anything is counted: the end of a run or a YouTube job, a job cancelled, an export
or a copy, the compare view opening, a saved edit, branch, restore or combine, a YouTube playlist added, a finished
download, *Update now*, a start of the app, and the assistants' log. Nothing else in the app is watched. Searches,
for one, happen in the page and aren't counted.

## What is never sent

- the recordings, or the transcripts and any of their text, titles or speaker names
- file names, paths, links (YouTube links too), API keys, the computer's name or the user's name
- error messages, or the length of one run on its own (only a week's totals, in whole minutes)
- what assistants asked: only how many requests they made and how many versions they saved
- any identifier that lasts: there is no install id, and the session number is new for each request

Every event is checked against the tables above as it leaves, and one with anything else is dropped instead of
sent.

## Where it goes

The events go to [Aptabase](https://aptabase.com), an analytics service for apps, in its EU region
(`eu.aptabase.com`), which is hosted in Germany. Its terms say that the data of EU-region accounts is processed and
stored only in the European Union, and that Aptabase processes it for the project as a processor under article 28
of the GDPR.

Like any server, Aptabase sees the computer's IP address, and Sedjem names itself in a `User-Agent` such as
`Linux/Ubuntu 24.04.4 LTS Sedjem/0.3.1 en-US`. According to Aptabase's
[privacy policy](https://aptabase.com/legal/privacy) and
[data processing agreement](https://aptabase.com/legal/dpa), the two are used only in memory, as the events arrive,
to find the country and region and to make a pseudonymous user hash for that day, with a salt that changes every
24 hours and is deleted within 48. Neither the IP address nor the `User-Agent` is stored. The events, with the
country and region, are kept for up to five years.

## Turning the counts off

Any of these turns them off:

- the switch *Send anonymous usage counts*, in *Settings → About* and in the first-start dialog
- `DO_NOT_TRACK=1` in the environment Sedjem is started from (any value but `0` or `false` counts)
- `SEDJEM_NO_USAGE=1`, the same for Sedjem alone

With one of the two variables set, the switch shows as off and can't be turned on. Turning the counts off deletes
the counts of weeks not sent yet and the events waiting to be sent, and what assistants ask meanwhile isn't counted
later. A copy of Sedjem without an app key in `app/config.toml` (`[usage] app_key`) counts and sends nothing and
shows no switch or notice.

## How it works

`usage.json` in the data folder keeps the day the install was counted, the month counted last, how far the
assistants' log has been counted, the counts of the weeks not sent yet (the times in seconds, the days of use as
days of the week), and the events made but not sent yet, at most 100, the oldest dropped first.

A thread in the background sends the waiting events to Aptabase, up to 25 in one request, and gives up on a request
after 10 seconds. Each event gets its time as it is sent, so one that waited is still taken. Events Aptabase
refuses (a `400`, an answer that it doesn't take the key, or any other error but the ones below) are dropped. When
there is no answer, or Aptabase asks to wait (`408`, `429` or a `5xx`), they are tried again an hour later. Sending
never slows the app down, and a failure isn't shown or logged.

The tests never send: while they run nothing is counted or sent, and a request to any address but the computer's
own is refused before it is made. On GitHub's machines the tests run as they do on a developer's computer, without
either variable; where the workflows start Sedjem itself, outside the tests (the check of the Windows steps from
source, the desktop builds and their checks), they set `SEDJEM_NO_USAGE=1`.

## Public totals

Aptabase has no way to read the counts back other than its dashboard and an export of all the events of a month.
So once a month, a few days into it, the events of the month before and of the month so far are exported as CSV
files (a late event is in the newer one), and `tools/usage_totals.py` turns them into the month's totals:

- installs: the active installs (the `active_month` events of the month), the new ones (`install`), the countries
  and regions, the systems and their versions, Sedjem's versions and the locales;
- retention: the active installs by `install_age`;
- the shares of each setting, the hardware, the size of the libraries, and the share of active installs with each
  model downloaded and each hosted service's key;
- weekly use, from the `week_summary` events of the weeks whose Thursday is in the month: the installs active each
  week, and per week of use the days used, the starts, the transcriptions and the hours of audio;
- the runs: by model, hosted service and speaker model, given or estimated speakers, length and language, the share
  on a graphics card and how often a graphics card fell back to the processor, and each model's speed (its
  processing minutes over its audio minutes);
- how many jobs failed, at each stage, or were cancelled, out of the jobs that ended;
- the features: the share of weeks of use with YouTube, editing, the compare view, combining, exports, copies and
  assistants, and their totals.

It adds them to `docs/usage.json`, and writes the newest month into the README and every month into the table
below.

Only totals are published. A country is listed only when at least three installs were active there that month, and
so is a region, a system, a version of a system or of Sedjem, and a locale; the others are added up as `other`, so
that no one install can be picked out. Since anyone can send events with the app key, which is public, a weekly
summary with numbers Sedjem never sends is left out, and a value Sedjem never sends counts as `unknown` (as does a
value missing from an event of an older version). The exports themselves, which have every event with its daily
user hash and session number, are deleted afterwards and never go into the repository. The script's own
description has the steps.

<!-- usage-totals:start -->
No totals yet: the counts start with Sedjem 0.3.0, and the first month's totals will be here once that month has
been exported.
<!-- usage-totals:end -->

## Files

| Path | Role |
|---|---|
| `app/usage.py` | the counts, the three events, what may be sent, and the sending |
| `app/static/usage.js` | the switch in *Settings → About* and the notice in the first-start dialog (`app/static/updates.js`) |
| `app/config.toml`, `[usage]` | the app key (`app_key`), and `host` for a self-hosted Aptabase |
| `settings.json` in the data folder | `usage_counts`: unset until the first-start dialog has said that the counts are on, then `true` or `false` |
| `usage.json` in the data folder | the day and month counted, how far the assistants' log was counted, the counts of weeks not sent yet, and the events waiting |
| `tools/usage_totals.py` | the public totals, from Aptabase's monthly exports |
| `docs/usage.json` | the public totals, month by month |
| `tests/test_usage.py`, `tests/test_ui_usage.py` | the tests: the events and their fields, each counting point, each period once, the switch and the variables, the sending against a stand-in for Aptabase, the totals, and the switch and the notice in the browser |

Scripts can read the switch in `GET /api/status`, whose `usage` field says whether this copy can send counts
(`available`), whether they are on (`on`), whether the first-start dialog is to say so (`ask`) and which variable
turns them off (`blocked`), and change it with `POST /api/settings` and `{"usage_counts": true}` or `false` (the
header `X-Sedjem: 1` is needed).
