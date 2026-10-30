# The pipeline

Every change to Sedjem is checked before it reaches main, and releases are built from commits whose tests
passed. The workflows are in [`.github/workflows/`](../.github/workflows/), the scripts they run in
[`.github/scripts/`](../.github/scripts/), and each workflow file says at its top what it does and why. This page
covers what runs when, how to read a failed run, how to cut a release and how to undo one, the security checks,
the rules for public text, and the choices still open.

## What runs when

| Workflow | When | What |
|---|---|---|
| Tests (`tests.yml`) | every push to main and every pull request | the quick checks, then the test suite on Linux and Windows and the setup from source on Windows |
| Desktop build (`desktop.yml`) | pull requests that change how the app is built or started, by hand, and when Release or Nightly preview calls it | the tests again, both builds with their self-tests, the installer and its update tests; publishes only when called or for a pushed tag |
| Release (`release.yml`) | by hand, with the version | checks the version and the commit, tags it, and has the Desktop build publish it |
| Nightly preview (`preview.yml`) | once a day | a pre-release of main's newest tested commit ([updates](12-updates.md#nightly-preview-builds)) |
| CodeQL (`codeql.yml`) | every push to main and pull request, and on Mondays | CodeQL's security queries on the Python, the JavaScript and the workflows |
| Security (`security.yml`) | every push to main and pull request, and on Mondays | pip-audit on the locks, every package the workflows install, and the dependency review of a pull request |
| Dependabot (`.github/dependabot.yml`) | on Mondays | pull requests that update the pip requirements and the actions |
| Pages (`pages.yml`) | pushes to main that change the website, after each day's stats, after a release, by hand | the website ([site/build.py](../site/build.py)), built from main; it never follows a run for a fork's pull request |
| Repository stats (`stats.yml`) | once a day | the numbers and charts on the `stats` branch |

The quick checks are one job, `checks`, of a minute or two, and the slower jobs of the Tests workflow start only
after it passes:

- the locks: each one in `.github/locks/` must have been made from the requirement files as they are
  ([Locks](#locks));
- ruff on the Python, with the rules in [`ruff.toml`](../ruff.toml): syntax errors and the pyflakes rules, such
  as undefined names and unused imports;
- `node --check` on every JavaScript file;
- shellcheck on the shell scripts;
- actionlint on the workflows, which also runs shellcheck on their bash steps;
- the links between the docs ([`tests/test_docs_links.py`](../tests/test_docs_links.py)) and the rules for
  public text ([`tests/test_public_text.py`](../tests/test_public_text.py)).

The Desktop build runs for a pull request that changes `desktop/`, a requirement file or a lock, how the app starts
(`app/__main__.py`, `app/desktop.py`, `app.sh`, `app.cmd`), a workflow, or a script the build runs. It builds
and checks, and never publishes then. Other changes wait for the nightly build.

A new push to a pull request cancels the runs it replaces. On main every commit keeps its own Tests run, since
the nightly preview and the Release workflow look for a passed run of the commit itself. The test jobs keep pip's
downloads in a cache between runs. The Desktop build doesn't: it publishes, and a cache written by an earlier run
could be restored into a release (CodeQL calls this cache poisoning), so each build downloads its packages afresh.
The browser tests drive the runner's own Chrome, so there is no Playwright browser to download or cache.

## Before main

main is protected: it can't be force-pushed or deleted, and a pull request can merge only when the checks
`checks`, `test (ubuntu-24.04)`, `test (windows-latest)` and `windows-source` have passed. `checks` must be
among them: when it fails, the jobs that need it are skipped, and GitHub counts a skipped check as passed.
Administrators aren't bound by the protection, so the maintainer may push a small fix straight to main; larger
changes, and every change made by an agent, go through a pull request, and the Tests workflow runs on main
again after the merge.

Commit titles start with the area they change, such as "Updates: ..." or "Website: ...". The release notes are
drafted from them.

## Reading a failed run

The checks of a pull request list each job. A job's page shows its log, and the run's summary page shows what
each job wrote there:

- Tests: a table of the tests that passed, failed, stopped with an error or were skipped, each failure with the
  end of its traceback, the slowest tests and, on Linux, the coverage by file. The summary comes from
  [`.github/scripts/run_tests.py`](../.github/scripts/run_tests.py).
- ruff and actionlint mark the lines they object to, so their findings also show in the pull request's *Files
  changed* tab.
- A browser test that failed leaves its Playwright trace and a screenshot of its page in the artifact
  `ui-failures-<system>` at the bottom of the run's page (kept for 14 days). `playwright show-trace <test>.zip`
  opens the trace, and so does [trace.playwright.dev](https://trace.playwright.dev), which reads the file in the
  browser without uploading it. The trace has every step of the test with the page before and after it, its
  console and its network requests.
- The Desktop build prints the self-tests' reports at the end of each build job, and keeps the Windows installer
  for 7 days, also when a later check of it failed, so that it can be tried by hand.

No test is retried automatically, since a retry would hide a test that fails now and then. When a failed job
passes after *Re-run failed jobs*, that test goes into an issue as a flaky test, with a link to the failed run.
The Latest update channel and the nightly preview skip any commit whose tests failed, so a red main holds them
back until the next commit passes.

The same checks run locally:

```sh
python .github/scripts/locks.py check                 # the locks
ruff check .                                          # pip install -r requirements-dev.txt
.venv/bin/python .github/scripts/run_tests.py --coverage
.venv/bin/python -m unittest tests.test_public_text -v
```

## Coverage

coverage.py measures which lines of Sedjem the tests run, in the Linux test job, and the summary shows the share
by file. It is measured and not enforced: no number fails a run. Code that runs only on Windows, in another
process (the model worker, the MCP server started by an assistant) or in the desktop build counts as not run.

## Cutting a release

1. Draft the notes: `python3 .github/scripts/release.py notes 0.3.2` writes `.github/release-notes/v0.3.2.md`
   from the titles of the commits since the previous release, grouped by their area, between the download
   section and the licence. Edit it: a first paragraph on what the release brings, the changes that matter to
   users, and the known limits.
2. Set the version in `app/__init__.py` and `desktop/installer.iss`, commit the three files as "Sedjem 0.3.2:
   the version, and the release notes", and bring the commit to main. Wait for its Tests run on main to pass.
3. Run the Release workflow: *Actions → Release → Run workflow* with the version, or
   `gh workflow run release.yml -f version=0.3.2`. It releases main's newest commit, or the commit given.

The Release workflow ([`.github/scripts/release.py`](../.github/scripts/release.py) makes its decisions):

- checks that the version is X.Y.Z, matches `app/__init__.py` at the commit, is newer than every release and has
  no release yet, that the commit is on main and that its Tests run passed on both systems. Its summary shows the
  notes as they will be published;
- tags the commit `v0.3.2`;
- calls the Desktop build workflow with the version and the commit, since a tag made with a workflow's token
  starts no other workflow. That builds and tests both systems, installs the Windows build over itself and over
  the newest release, both the way *Update now* does, and publishes the release with its files, its notes and a
  link to the privacy policy. Without a notes file, the draft is published as it is. The release then goes to
  winget.

Running the workflow is the approval. Publishing uses the GitHub environment `release`, which GitHub creates on
its first use without rules. To have a release wait for a click before it is published, add a required reviewer
in *Settings → Environments → release*; the run then waits at the Desktop build's release job, the notes can be
read in the summary meanwhile, and *Review deployments* lets it go on. A tag pushed by hand
(`git push origin v0.3.2`) still publishes through the Desktop build, with the same approval, but without the
Release workflow's checks.

## Undoing a release

Before it is published (a build failed, or the approval was refused), the tag stays. Fix the problem on main and
run the Release workflow again with the same version: from the same commit it goes on from the tag. To release
another commit, delete the tag first, with `git push origin --delete v0.3.2`.

Once published, the release is what every copy of Sedjem is offered when it checks for updates. Publish a fixed
0.3.3 as soon as possible rather than replacing 0.3.2, since copies that installed 0.3.2 wouldn't be offered a new
build with the same number. Meanwhile, `gh release delete v0.3.2 --cleanup-tag` takes it back, and the release
before it becomes the newest again; a copy that already has 0.3.2 keeps it until the next version. Close the
pull request to microsoft/winget-pkgs if it is still open; once it is merged, the next version's replaces it.

## Security

- Dependabot opens a pull request a week for the actions and one for the minor and patch releases of the Python
  packages; a new major version comes alone. Each goes through the Tests workflow like any other change. The speech
  engines and the audio libraries they read with (faster-whisper, CTranslate2, PyAV, sherpa-onnx, transcribe-cpp,
  soundfile, ONNX Runtime) come in a pull request of their own, any kind of release: the tests use stand-ins for the
  models, so a passing run says nothing about the transcripts, and that pull request is merged only after the
  models have run on real recordings with the new versions. It
  leaves a range such as `av>=11,<19` alone while new releases fit it, and never proposes PyAV 19, which
  faster-whisper 1.2.1 can't use. Its pull requests change the requirement files, not the locks, so their quick
  checks fail until the locks are made again on their branch ([Locks](#locks)).
- pip-audit looks up every package of every lock, those that only Windows installs too, in the Python Packaging
  Advisory Database and OSV, also once a week, since a vulnerability can be published for a version already in
  use.
- The dependency review fails a pull request that adds a dependency with a known vulnerability of high severity
  or more.
- CodeQL's security queries run on the Python, the JavaScript and the workflows, with the results in the
  repository's Security tab. This is CodeQL's advanced setup, so its default setup in the settings stays off.
- Nothing the workflows install or run can change without a commit that changes it
  ([ADR 14](adr/0014-everything-the-builds-install-is-pinned-by-hash.md)). Every action is pinned to a commit,
  with its version in a comment, and Dependabot updates both. Every Python package comes from a lock with the
  SHA-256 of its files ([Locks](#locks)). A program a run downloads is checked against its SHA-256 before it
  runs: actionlint, wingetcreate, Inno Setup 6.7.1 when the runner has no Inno Setup of its own, and the newest
  release's installer, which the update test installs, against that release's `SHA256SUMS.txt` as *Update now*
  does. The scripts the nightly preview and the stats fetch are this repository's own, at the run's commit.
  [`tests/test_pipeline.py`](../tests/test_pipeline.py) checks each of these, and that no other installer runs.
- Each workflow starts with no permissions or read-only ones, and each job gets only what it needs. Checkouts
  don't keep the token.
- Pull requests from forks run with `pull_request`, which gives them a read-only token and no secrets. No
  workflow uses `pull_request_target`. The two secrets are used in one place each: `WINGET_TOKEN` in the winget
  job of a release, and `STATS_TOKEN` in the stats workflow.
- GitHub's own secret scanning and push protection, free for public repositories, look for leaked keys. A
  separate scanner such as gitleaks would add generic patterns, which here would mostly find the tests' stand-in
  keys and the public key of the usage counts, so there is none.

### Locks

The requirement files name the packages Sedjem needs, at the versions it wants. The locks in
[`.github/locks/`](../.github/locks/) add every package those need in turn, each pinned to a version and to the
SHA-256 of each of its files, for Linux and Windows on Python 3.12, the workflows' Python.
[`.github/scripts/locks.py`](../.github/scripts/locks.py) makes them with uv, in one resolution for both systems
(`uv pip compile --universal --generate-hashes`), then resolves them again for each system and adds what one needs
that the first resolution missed: sherpa-onnx's wheels need sherpa-onnx-core, which its source archive doesn't name
([AgDR-0012](agdr/AgDR-0012-locks-from-uv-universal-checked-per-system.md)). The workflows install only from the
locks, with `pip install --require-hashes`, which refuses any file whose hash isn't in the lock:

| Lock | Made from | Installed by |
|---|---|---|
| `ci.txt` | `requirements.txt`, `requirements-desktop.txt` and `requirements-dev.txt` | the test jobs, and the Desktop build, which bundles what it installs |
| `source.txt` | `requirements.txt` and `requirements-desktop.txt`, at `ci.txt`'s versions | the setup from source on Windows (`windows-source`), with pip and with uv |
| `setuptools.txt` | setuptools, at `ci.txt`'s version | the jobs that install `ci.txt` or `source.txt`, first |
| `tools.txt` | ruff at `requirements-dev.txt`'s version, uv and pip-audit at those in `tools.in` | the quick checks, the Security workflow, and `windows-source` for uv |

pywebview needs proxy-tools, which comes only as source code, so installing it builds it. For an isolated build pip
would fetch the newest setuptools without checking a hash, so setuptools comes first, from its lock, and pip builds
with it (`--no-build-isolation`); uv checks the build's setuptools against the same lock
(`--require-build-hashes`). The runner's pip is never upgraded. Where `tools.txt` is installed, pip comes with
pip-audit's dependencies, pinned by hash like the rest.

To change a requirement, edit its file, make the locks again, and commit both. A package keeps the version it has
in a lock while the requirements allow it, so the locks change only where the requirement files did:

```sh
python -m pip install --require-hashes -r .github/locks/tools.txt   # uv at the locks' version, once
python .github/scripts/locks.py                                     # the locks, made again
python .github/scripts/locks.py --upgrade-package urllib3           # one package to its newest version allowed
python .github/scripts/locks.py --upgrade                           # every package
```

The last line of each lock's header holds the SHA-256 of what it was made from and of the lock itself. The quick
checks compare it (`python .github/scripts/locks.py check`), so a requirement changed without its locks, or a lock
changed by hand, fails within a minute, with the command to run.

Dependabot proposes the requirement files' updates as before, but can't make the locks: it makes such locks only
from `.in` files that hold the pins, and Sedjem's pins are in the requirement files that setups from source and
*Update now* install ([AgDR-0013](agdr/AgDR-0013-locks-kept-current-by-a-check-not-dependabot.md)). On each of its
pull requests for the Python packages, run `python .github/scripts/locks.py` on its branch and push the locks, which
starts the checks again. `@dependabot recreate` drops that commit, and the locks have to be made again. Packages
that only a lock names, the dependencies' dependencies, get no pull request from Dependabot. pip-audit says when one
has a known vulnerability, and `--upgrade-package` moves it.

Setups from source and *Update now* still install the requirement files
([AgDR-0014](agdr/AgDR-0014-source-setups-keep-the-requirement-files.md)), so a setup from source gets the newest
versions of the dependencies' dependencies, as before. A setup from source on Windows gets the versions of the
checks instead with `.venv\Scripts\pip install --require-hashes -r .github/locks/setuptools.txt`, then
`.venv\Scripts\pip install --require-hashes --no-build-isolation -r .github/locks/source.txt`.

What the repository doesn't pin is the runner's own software: Python with its pip, Node.js, Chrome, shellcheck, gh,
and Inno Setup when the image has it, which GitHub updates with the image. The speech models that the Desktop
build's real model test downloads are data that Sedjem reads, as on any computer.

## Public text

The repository is public. [`tests/test_public_text.py`](../tests/test_public_text.py) checks every file in git:

- no names of the private meetings or of the tools they came from. The test holds them as SHA-256 hashes, so
  that it doesn't name them either;
- no paths into someone's home folder; examples use a made-up user, such as `/home/you`;
- docs without bold or em dashes, in every Markdown file but the [research](research/) material, and in the
  issue forms.

## Signing

The Windows installer isn't signed, so a browser download of it shows SmartScreen's warning about an unknown
publisher ([desktop app](10-desktop.md#not-done-yet)). SignPath Foundation, which signs for open-source projects
at no cost, accepts only projects without commercial dual-licensing, and Sedjem offers commercial licences
besides the AGPL. A paid certificate isn't bought for now, so the pipeline has no signing step. Once there is
one, signing goes into the Desktop build before the installer is uploaded, so that the update tests run on the
signed file.

## Open choices

These were decided for now, and can change:

- Pull requests: required for agents and larger changes; the maintainer may push small fixes to main.
- The linter: ruff with the error rules only, the existing code fixed in one go. More rules can follow the same
  way, one set at a time.
- Approval: running the Release workflow is the approval, and the `release` environment has no reviewer yet.
- Signing: none for now, as above.
- Provenance: the release files have no build provenance yet. GitHub's attest-build-provenance action would let
  anyone check that a download came from the Desktop build workflow (`gh attestation verify`); it can only be
  tried with a real release, so it comes in a change of its own.
