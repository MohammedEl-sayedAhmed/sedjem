# Usage counts

Sedjem sends a few anonymous counts to Aptabase, so that the project knows how many installs use it and in which
countries, which versions and systems they run, and what they use: which models, local or hosted, speaker labels,
exports and YouTube. The counts are on by default and can be turned off at any time. Nothing is counted or sent
before Sedjem has said so: on first start, the dialog that asks about updates also says that the counts are on and
has their switch, and a copy updated from a version without them shows the same notice once.

This page lists everything that is sent and when, what is never sent, where it goes, how to turn the counts off,
and how the public totals are made from them.

## When something is sent

| Event | When | What it adds |
|---|---|---|
| `install` | once for each data folder, the first time the counts are allowed | nothing |
| `active_month` | once a calendar month, the first time Sedjem runs that month | the kind of install, the window, and the computer in broad ranges |
| `week_summary` | once for each week in which Sedjem was used, the first time it runs after that week ended | how many transcriptions and minutes of audio, by model, and the other counts below |

Months and weeks are counted in UTC, and weeks are ISO weeks, Monday to Sunday. An install used every week sends
about six events a month. Sedjem looks about twenty seconds after it starts and then every fifteen minutes while it
runs, so a month or a week that begins while it is open is counted too.

## What each event holds

Every event has its name, the time it was made (UTC, to the millisecond), a session number and these details of
the system:

| Field | Value |
|---|---|
| `osName` | `Windows`, `Linux` or `macOS` |
| `osVersion` | on Windows the version number (`10.0.26100`), on Linux the name in `/etc/os-release` (`Ubuntu 24.04.4 LTS`), on macOS its version |
| `locale` | the system's language and region, such as `ar-EG` or `en-US` |
| `appVersion` | the version *Settings → About* shows, such as `0.3.0` (a nightly preview's is like `0.3.1-preview.20261101`) |
| `engineName`, `engineVersion` | `python` and the version of Python, such as `3.12.15` |
| `sdkVersion` | `sedjem-usage@1` |
| `isDebug` | `false`, or `true` when `SEDJEM_USAGE_DEBUG=1` is set, for trying the counts out (Aptabase keeps those apart from the others) |

The session number is the second the events were made followed by eight random digits. A new one is drawn each time
Sedjem makes events, so it ties together only the events made at the same moment.

`active_month` adds these, each one of a few fixed values:

| Prop | Values | Meaning |
|---|---|---|
| `kind` | `installer`, `linux-build`, `source`, `other` | installed with the Windows installer, the Linux build, run from the source code, or another desktop build |
| `ui` | `desktop`, `browser` | started as the desktop app (`Sedjem`, `python -m app.desktop`, `./app.sh --desktop`), or with `./app.sh` in a browser tab |
| `gpu` | `nvidia`, `amd`, `intel`, `other`, `none` | the maker of the graphics card that Sedjem's own check found and can use, NVIDIA first, then AMD, then Intel when there are several; `none` when it found none or couldn't look |
| `ram` | `under 8`, `8-16`, `16-32`, `over 32` | the memory in GB: a computer with 8 or 16 GB counts as `8-16`, one with 24 or 32 GB as `16-32` |
| `cores` | `under 4`, `4-8`, `8-16`, `over 16` | the processor's threads: 4 to 8 count as `4-8`, 9 to 16 as `8-16` |

`week_summary` adds the week, as `week` (such as `2026-W44`), and these whole numbers, counted on the computer
during that week:

| Prop | What it counts |
|---|---|
| `transcriptions` | model runs that finished, local or hosted (taking a YouTube video's captions made by people is not a model run) |
| `minutes` | the length of their audio, added up and rounded to whole minutes |
| `cohere`, `whisper_medium`, `whisper_large`, `other_local`, `hosted` | the same runs by model: Cohere Transcribe Arabic, whisper-medium code-switching, Whisper large-v3, any other local model (one added from Hugging Face, say), and any hosted service |
| `speakers` | runs whose transcript has speaker labels |
| `nemotron` | runs whose speaker labels came from Nemotron-3-Diarization |
| `youtube` | YouTube videos transcribed, by a model or from their captions |
| `compares` | times the compare view was opened |
| `failed` | runs that failed, YouTube downloads that failed, and recordings that couldn't be read |
| `txt`, `srt`, `vtt`, `md`, `json` | exports in each format, downloaded or saved (copying the text isn't counted) |

These are the only places where anything is counted: the end of a run or a YouTube job, an export, and the compare
view opening. Nothing else in the app is watched.

## What is never sent

- the recordings, or the transcripts and any of their text, titles or speaker names
- file names, paths, links (YouTube links too), API keys, the computer's name or the user's name
- error messages, or the length of one run on its own (only a week's total, in whole minutes)
- any identifier that lasts: there is no install id, and the session number is new each time events are made

Every event is checked against the tables above as it leaves, and one with anything else is dropped instead of
sent.

## Where it goes

The events go to [Aptabase](https://aptabase.com), an analytics service for apps, in its EU region
(`eu.aptabase.com`), which is hosted in Germany. Its terms say that the data of EU-region accounts is processed and
stored only in the European Union, and that Aptabase processes it for the project as a processor under article 28
of the GDPR.

Like any server, Aptabase sees the computer's IP address, and Sedjem names itself in a `User-Agent` such as
`Linux/Ubuntu 24.04.4 LTS Sedjem/0.3.0 en-US`. According to Aptabase's
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
the counts of weeks not sent yet and the events waiting to be sent. A copy of Sedjem without an app key in
`app/config.toml` (`[usage] app_key`) counts and sends nothing and shows no switch or notice.

## How it works

`usage.json` in the data folder keeps the day the install was counted and the month counted last, the counts of the
weeks not sent yet, and the events made but not sent yet, which wait there while the computer is offline.

A thread in the background sends the waiting events to Aptabase, up to 25 in one request, and gives up on a request
after 10 seconds. Events Aptabase refuses (a `400`, an answer that it doesn't take the key, or any other error but
the ones below) are dropped. When there is no answer, or Aptabase asks to wait (`408`, `429` or a `5xx`), they are
tried again an hour later, and dropped once they are a day old, since Aptabase takes nothing older. Sending never
slows the app down, and a failure isn't shown or logged.

The tests never send: while they run nothing is counted or sent, and a request to any address but the computer's
own is refused before it is made. The workflows that start Sedjem on GitHub's machines (the tests, the desktop
builds and their checks) also set `SEDJEM_NO_USAGE=1`.

## Public totals

Aptabase has no way to read the counts back other than its dashboard and an export of all the events of a month. So
once a month the events of the month before are exported as a CSV file, and `tools/usage_totals.py` turns them into
totals: the active installs (the `active_month` events), the new ones (`install`), the countries, the systems and
versions, the transcriptions and hours of audio (from the `week_summary` events that arrived that month), the share
of each model and of hosted services, and the other counts. It adds them to `docs/usage.json`, and writes the newest
month into the README and every month into the table below.

Only totals are published. A country is listed only when at least three installs were active there that month, and
so is a system, a version of a system or a version of Sedjem; the others are added up as `other`, so that no one
install can be picked out. Since anyone can send events with the app key, which is public, a weekly summary with
numbers Sedjem never sends is left out, and a value Sedjem never sends counts as `other`. The export itself, which
has every event with its daily user hash and session number, is deleted afterwards and never goes into the
repository. The script's own description has the steps.

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
| `usage.json` in the data folder | the day and month counted, the counts of weeks not sent yet, and the events waiting |
| `tools/usage_totals.py` | the public totals, from Aptabase's monthly export |
| `docs/usage.json` | the public totals, month by month |
| `tests/test_usage.py`, `tests/test_ui_usage.py` | the tests: the events and their fields, each period once, the switch and the variables, the sending against a stand-in for Aptabase, the totals, and the switch and the notice in the browser |

Scripts can read the switch in `GET /api/status`, whose `usage` field says whether this copy can send counts
(`available`), whether they are on (`on`), whether the first-start dialog is to say so (`ask`) and which variable
turns them off (`blocked`), and change it with `POST /api/settings` and `{"usage_counts": true}` or `false` (the
header `X-Sedjem: 1` is needed).
