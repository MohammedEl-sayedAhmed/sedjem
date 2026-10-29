# Updates

Sedjem can tell when a newer version is out and, when asked, update itself. It follows one of two
channels: stable releases, or the latest tested changes on main even before they are released. Checking
contacts GitHub, so Sedjem asks before it checks on its own, and nothing is downloaded or installed
without a click.

## The two channels

*Settings → Updates* chooses the channel. *Stable releases* is the default.

| Channel | From source (a git checkout) | Desktop app |
|---|---|---|
| *Stable releases* | the newest release on GitHub that is not a draft or a pre-release, if its version is above the running one | the same |
| *Latest changes* | the newest commit on main whose Tests workflow passed on Windows and Linux | the newest nightly preview build or release whose version is above the running one |

A commit whose tests are still running, or failed, is never offered. On *Latest changes* a git
checkout is offered nothing when it is up to date with the newest tested commit, when it already has
newer commits of its own (ahead), or when it and main have each moved on separately (diverged).
*Settings* says which.

The desktop app knows its own version and commit: the build stamps them into the app
(`app/_build.json`, written by `desktop/build.py`). From source, the commit is read from the files in
`.git`, so the version shows even where the git program is missing.

## Asking first

On first start Sedjem asks once: *Check for updates automatically?* *Yes, check daily* lets it check in
the background, and with *No* it checks only when asked. The answer is the switch *Check for updates
automatically* in *Settings → Updates*, which can change it later. *Check now* in the same place works
either way. The same dialog says that Sedjem sends anonymous usage counts, which have nothing to do with
GitHub, and has their switch ([usage counts](13-usage-counts.md)).

With the switch on, Sedjem checks about ten seconds after it starts (so starting stays fast) and then
once a day while it runs. A check that fails because the computer is offline is tried again an hour
later. It stays quiet about it: *Settings* shows *Couldn't check (offline)* instead.

## What is sent to GitHub

A check asks GitHub's REST API (`api.github.com`) for public information about this repository,
without logging in. The requests are:

| What runs | Channel | Requests |
|---|---|---|
| Any | *Stable releases* | the list of releases |
| Desktop app | *Latest changes* | the list of releases; when a newer one is found, the comparison between the build's commit and that release's |
| Git checkout | *Latest changes* | the newest successful run of the Tests workflow for a push to main; the comparison between the checkout's commit and that run's |
| Git checkout | *Stable releases* | the list of releases; when a newer one is found, the commit its tag points to and the comparison with the checkout's commit |

Each request carries a `User-Agent` header that says `Sedjem` (GitHub asks every program to name
itself), the API version, and the `ETag` of the previous answer to the same request. GitHub sees the
computer's IP address, as with any connection, and the addresses asked for. A comparison's address
holds the commit that is running, so it tells GitHub which version that is. Nothing else is sent:
no transcriptions, settings, names or details of the computer.

Without a login GitHub allows 60 requests an hour from one address, and a check makes one to three.
An answer that hasn't changed since the last check comes back empty (`304 Not Modified`) and doesn't
count; the last answers are kept in `updates.json` in the data folder. When the limit is reached
anyway, *Settings* says so and the next check waits until GitHub takes requests again.

## In the app

- The top bar shows a pill when something newer is out: *Update available · 0.3.0* on *Stable
  releases*, or *12 new changes* on *Latest changes*. It opens *Settings → Updates*.
- When something new first appears, a notice says *Sedjem 0.3.0 is out* (or *12 new changes on main*),
  with *What's new* and *Later*. It shows once for each version or commit.
- *Settings → Updates* shows the running version and commit, the switch, the channel, *Checked 2 hours
  ago* with *Check now*, and what is new: the release notes on *Stable releases*, the list of changes
  with their dates and links on *Latest changes*. Its buttons are *Update now*, *Download* (the release
  or comparison page on GitHub) and *Skip this version*.
- *Skip this version* hides the pill and the notice for that version, or that commit on *Latest
  changes*. *Settings* still lists it, marked as skipped, and a newer one shows again.

## Update now

*Update now* runs only when it is clicked, one update at a time, and *Settings* shows its progress.
What it does depends on how Sedjem was installed. An update that restarts Sedjem doesn't start while a
transcription is running.

### From source

A git checkout updates itself when it is on the branch main, has no local changes to the files git
tracks (files git doesn't track don't count), has a remote called origin, and the git program is
installed. Then Sedjem:

1. fetches main (`git fetch origin main`);
2. checks that the offered commit is on main at origin and that the checkout's commit comes before it,
   so that the update is a fast-forward;
3. installs the requirement files that changed into the Python environment Sedjem runs in. That is
   `requirements.txt` always, and `requirements-desktop.txt` or `requirements-dev.txt` when a package
   they name is installed there. It uses `uv pip install -p <python>` when uv is installed (an
   environment made with uv has no pip), else `python -m pip install`. This happens before the merge,
   so if it fails the checkout stays as it was;
4. moves the checkout to the offered commit (`git merge --ff-only <commit>`);
5. starts again with the same command line and port, and the old process quits. The open page waits
   and reloads once the new one answers. If the new process stops right away (the updated code can't
   start), the old one keeps running and *Settings* says so.

When Sedjem was started from a terminal, the new process runs in the background after the restart and
writes what it prints to `sedjem.log` in the data folder. *Quit Sedjem* in *Settings* stops it.

When one of the conditions isn't met (local changes, another branch, no git, or neither uv nor pip for
changed requirements), *Settings* says why and lists the exact commands to run instead (on Windows, for PowerShell).

### The Windows installer

Sedjem downloads `Sedjem-<version>-setup.exe` from the release into the `updates` folder in the data
folder, checks it against the release's `SHA256SUMS.txt` (a download that doesn't match is deleted),
and runs it with `/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /UPDATE=1`. Sedjem quits, the
installer installs the new version over the old one, and then starts it. The transcriptions, keys and
models in `%LOCALAPPDATA%\Sedjem` are not touched. Sedjem downloads the installer itself rather than through
a browser, so the file doesn't carry the mark that browsers add to downloads (the Mark of the Web), and Windows
SmartScreen doesn't warn about it, although it isn't signed. The installer shows no page when it runs this way,
and leaves the usage counts' setting as it is. The downloaded installer is deleted the next time Sedjem starts.

### The Linux build

Sedjem downloads `Sedjem-<version>-linux-x64.tar.gz` and checks it the same way, then unpacks it beside
the running build as `Sedjem-<version>/` and opens that folder in the file manager. Nothing in the
archive can be written outside that folder. The new version starts with `Sedjem-<version>/Sedjem`, and
the old folder can be deleted once it runs. Replacing the running build in place may come later.

### Other copies

A copy that isn't a git checkout, a Windows copy that the installer didn't put there, or a build for
another system can't update itself. *Download* opens the release page instead.

Only HTTPS connections to GitHub are made, and only this repository's release files are downloaded.
Each one is checked against the release's `SHA256SUMS.txt` before it is used.

## Updating by hand from source

In the project folder, with no local changes:

```sh
git switch main
git pull --ff-only origin main
uv pip install --python .venv -r requirements.txt     # an environment made with uv
```

On Windows add `-r requirements-desktop.txt` to the last step. With pip instead of uv it is
`.venv/bin/pip install -r requirements.txt` on Linux and
`.venv\Scripts\pip install -r requirements.txt -r requirements-desktop.txt` on Windows.
Then start Sedjem again. `git pull` takes the newest commit on main, whose tests may not have passed
yet. To take the tested commit that *Settings* offers instead, run `git fetch origin main` and
`git merge --ff-only <commit>` in place of the pull.

## Nightly preview builds

A scheduled workflow runs once a day. When main has moved since the last preview and the Tests workflow
passed for that commit, it builds the Windows installer and the Linux archive and publishes them as a
pre-release named after the next version and the date, for example `v0.2.1-preview.20261021` (a
second one on the same day gets `.2`), with a `SHA256SUMS.txt`. Previews older than a week are
removed; releases are never touched. A preview sorts after the release before it and before its own
release, so the desktop app on *Latest changes* moves from preview to preview and then to the release.

## Files

| Path | Role |
|---|---|
| `app/updates.py` | the checks, the background check, *Update now* and the restart |
| `app/config.toml`, `[updates]` | the repository (`repo`) and GitHub's API (`api`) |
| `settings.json` in the data folder | `update_check` (unset until the first-start question is answered), `update_channel`, `update_skip` |
| `updates.json` in the data folder | the last check's result and the answers' ETags |
| `updates/` in the data folder | downloads, deleted the next time Sedjem starts |
| `tests/test_updates.py`, `tests/github_support.py` | the tests, against a stand-in for GitHub's API and throwaway git repositories |
| `tests/test_ui_updates.py`, `tests/test_ui_updates_e2e.py` | the browser tests of the question, the pill, the notice and *Settings → Updates*, the second with the real checks against the stand-in |

Scripts can use the same calls as *Settings* (every change needs the header `X-Sedjem: 1`):
`GET /api/updates` for the state, and `POST /api/updates/check`, `/api/updates/settings`
(`{"enabled", "channel"}`), `/api/updates/skip` (`{"skip"}`) and `/api/updates/install`.
`GET /api/status` has an `update` field with what the top bar needs.
