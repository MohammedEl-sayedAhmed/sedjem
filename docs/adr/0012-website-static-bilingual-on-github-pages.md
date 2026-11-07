---
status: Accepted
date: 2026-10-30
deciders: MohammedEl-sayedAhmed
---

# 12. A static, bilingual website on GitHub Pages

## Context

[Issue #12](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/12) asked for a small website for people who won't
read a README: what Sedjem does, who it is for and how to get it. It asked for plain HTML and CSS with no framework,
built by a script with Python's standard library only, published by a workflow, in the app's look, loading nothing
from other sites. An Arabic version was an open question, and the issue asked for no analytics.

## Decision

From [c46cf0c](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/c46cf0c)
([PR #26](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/26)):

- One page, in English at `/` and in Egyptian Arabic at `/ar/`, made by `site/build.py` from one template and the
  words of `site/copy/en.toml` and `site/copy/ar.toml`. The Arabic page is set right to left rather than flipped.
- `.github/workflows/pages.yml` builds it, runs the site's tests on the result and publishes it to GitHub Pages
  after each push to main that changes anything the build reads.
- The text, the transcript in the hero included, is in the HTML when the page is built, so the page reads without
  JavaScript. The fonts are served with the site, and the pages load nothing from other sites. The design notes are
  in `site/DESIGN.md`.
- From [0ade7f3](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/0ade7f3)
  ([PR #28](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/28)), each page counts its visits and download
  clicks anonymously, so that the project knows how people find it and what they download. The counts go to
  Aptabase, as the app's do ([ADR 10](0010-anonymous-usage-counts-through-aptabase.md)). They set no cookie and use
  no storage, send nothing when the browser asks for Do Not Track or Global Privacy Control, and are sent only from
  the published host ([the website](../privacy.md#the-website)).

## Consequences

- The site has no server of its own and no framework to keep up, and a change to it is a change to the repository,
  checked like any other.
- Both languages share one structure, and the tests compare the two pages.
- The page counts reverse the issue's "no analytics": they are the one thing the pages send to another site, and
  the privacy policy lists each value.
- GitHub sees each visit as any web host does.
