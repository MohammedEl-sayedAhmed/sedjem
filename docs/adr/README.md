# Architecture decision records

This folder holds Sedjem's architecture decision records (ADRs): the decisions the maintainer made that later
changes have to respect, each with its context, the decision and its consequences. Decisions an AI coding agent made
while building Sedjem are kept apart, as agent decision records in [docs/agdr](../agdr/README.md).
[ADR 1](0001-record-decisions-as-adrs-and-agdrs.md) says which kind goes where and when to write one.

Each record is a Markdown file named `NNNN-kebab-case-title.md`, numbered in order, with a short frontmatter
(`status`, `date`, `deciders`) and three sections: Context, Decision and Consequences. The format follows
[MADR](https://adr.github.io/madr/). A status is Proposed, Accepted, Deprecated or Superseded by NNNN. An accepted
record isn't edited: a decision that changes gets a new record, and the old one's status becomes Superseded by the
new number.

The records up to 13 were written on 30 October 2026, after the decisions, from the docs, commits, issues and pull
requests they cite; each is dated by the commit that brought its decision in.

## Index

| # | Title | Status | Summary |
|---|---|---|---|
| 0001 | [Record decisions as ADRs and AgDRs](0001-record-decisions-as-adrs-and-agdrs.md) | Accepted | The maintainer's decisions in docs/adr, an agent's in docs/agdr; a changed decision gets a new record that supersedes the old one. |
| 0002 | [Recordings and transcripts stay on the computer](0002-recordings-and-transcripts-stay-on-the-computer.md) | Accepted | Local open models by default; a hosted service only with the user's key and a confirmation for each recording. |
| 0003 | [The whisper-medium code-switching fine-tune as the default model](0003-whisper-medium-code-switching-as-the-default-model.md) | Accepted | It keeps English terms in English best; Cohere Transcribe Arabic is the faster second model for Arabic-heavy audio. |
| 0004 | [AGPL-3.0-only, with commercial licences from the author](0004-agpl-with-commercial-licences.md) | Accepted | The AGPL with an author credit replaces MIT, and commercial licences are offered for closed use. |
| 0005 | [YouTube: captions made by people, and no summaries inside the app](0005-youtube-captions-made-by-people-no-summaries.md) | Accepted | Captions made by people become the transcript, automatic ones never; courses are saved as files for an assistant to summarise. |
| 0006 | [Assistants over MCP, on the computer and read-only](0006-assistants-over-mcp-local-and-read-only.md) | Accepted | An MCP server over stdio, with no network port, reads what Settings lets assistants see. |
| 0007 | [Updates: asked first, two channels, and only tested builds](0007-updates-asked-first-two-channels-tested-builds.md) | Accepted | Sedjem asks before checking; stable releases or main's newest tested commit; nothing installs without a click. |
| 0008 | [Branches, and one main transcript per recording](0008-branches-and-one-main-transcript-per-recording.md) | Accepted | Model runs are branches, one main transcript is curated, a stale save is refused, and assistants save only when allowed. |
| 0009 | [Speaker labels by voice, with TitaNet-small as the default](0009-titanet-small-stays-the-default-speaker-model.md) | Accepted | After the comparison with Nemotron-3-Diarization, TitaNet-small stays the default and Nemotron is the choice for larger meetings. |
| 0010 | [Anonymous usage counts through Aptabase](0010-anonymous-usage-counts-through-aptabase.md) | Accepted | A few counts a month, on by default with a switch, never any content or identifier, with public monthly totals. |
| 0011 | [Windows: no free code signing, and winget instead](0011-windows-no-free-signing-ship-through-winget.md) | Accepted | The commercial licences rule out SignPath; the installer stays unsigned and Sedjem is published on winget. |
| 0012 | [A static, bilingual website on GitHub Pages](0012-website-static-bilingual-on-github-pages.md) | Accepted | One page in English and Egyptian Arabic, built with the standard library, with anonymous page counts and no cookies. |
| 0013 | [Branch protection on main for a single maintainer](0013-branch-protection-for-a-single-maintainer.md) | Accepted | The tests on Linux and Windows are required, force pushes and deletion are blocked, and no review is required. |
| 0014 | [Everything the builds install is pinned by hash](0014-everything-the-builds-install-is-pinned-by-hash.md) | Accepted | Every Python package from a lock with its SHA-256, dependencies' dependencies included; pip never upgraded; every program a run downloads checked first, as clipman pins its actions. |

## Adding a record

1. Take the next free number and a short kebab-case title, and copy the frontmatter and sections of an existing
   record.
2. Write the context, the decision and its consequences in plain prose, citing the doc page, commit, issue or pull
   request that shows each fact. Bold and em dashes are not used.
3. To change an earlier decision, say in the new record which one it replaces, and set the old record's status to
   Superseded by the new number, here and in its file.
4. Add a row to the index above, with the same title and status as the file, in the pull request that carries the
   decision.
5. Run `python -m unittest tests.test_decisions`, which checks both folders.
