# Contributing to Sedjem

Contributions are welcome: a bug report, an idea, a fix or a new feature. This page says how to send one so that it
can be merged.

## Before you start

- A bug or an idea goes in [the issues](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/new/choose), on its
  form. A question goes in [the Discussions](https://github.com/MohammedEl-sayedAhmed/sedjem/discussions).
- A security problem never goes in a public issue: [the security policy](SECURITY.md) says how to report it privately.
- A small fix, such as a typo or a broken link, can go straight to a pull request. For a feature or a larger change,
  open an issue first and say what you plan, so that the work isn't wasted.
- Everyone who takes part follows [the code of conduct](CODE_OF_CONDUCT.md).

## The agreement for your contribution

Sedjem is free software under the GNU Affero General Public License, and its author also offers it under commercial
licences. So that your contribution can be part of every version, you accept the
[Contributor Licence Agreement](CLA.md) once, before your first pull request is merged: you keep your copyright, and
the author may use your contribution under any of the licences Sedjem is offered under. To accept it, write this in a
comment on your first pull request:

> I have read the Contributor Licence Agreement (CLA.md) and I agree to it.

The name and the logo are the author's trademarks; [their page](TRADEMARKS.md) says how a fork may use them.

## Set up, run and check

[AGENTS.md](AGENTS.md) has the commands. It is written for coding agents and for anyone new to the code:

- [Set up](AGENTS.md#set-up): a Python 3.12 environment made with uv, and the requirements.
- [Test and check](AGENTS.md#test-and-check): the tests, ruff, the locks check and the other checks that CI requires.
- [Layout](AGENTS.md#layout): where each part of the app is.

[How Sedjem fits together](docs/16-architecture.md) has diagrams of the parts.

## A pull request

- One change per pull request, from a branch of your fork.
- Commit titles start with the area they change, such as "Docs: ...", "Models: ..." or "Website: ...", because the
  release notes are drafted from them.
- Tests come with the change, and every check in [Test and check](AGENTS.md#test-and-check) passes. CI runs them again,
  on Ubuntu and on Windows.
- A changed requirement file comes with its locks, made with `python .github/scripts/locks.py`
  ([locks](docs/14-pipeline.md#locks)).
- Docs say what changed, in plain prose: no bold, no em dashes, British spelling
  ([public text](docs/14-pipeline.md#public-text)).
- Nothing private: no recordings, transcripts or API keys, and no names or paths from your own computer.
- Using an AI tool is fine, but the history doesn't name it: no co-author line for an assistant and no "Generated
  with" note, in a commit or in the pull request ([ADR 15](docs/adr/0015-no-ai-tool-attribution-in-the-history.md)).
  The `footprints` check fails a pull request that has one.

The pull request template has these as a checklist. A pull request is merged by squashing, once CI passes and its
review is done.
