---
id: AgDR-0006
timestamp: 2026-10-30T15:32:22Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Small lines on the website's number cards, as monotone curves drawn at build time

> In the context of the figure cards for the website's public numbers in PR #30, facing a small line under the cards for running totals, such as the downloads and the stars, and for the daily page views, I decided to draw each line when the page is built, as SVG, with a monotone cubic through the days (Fritsch and Carlson) to achieve a smooth line that never overshoots a value, on a page that needs no chart script, accepting more drawing code in site/build.py than the straight segments of the stats branch's charts, and a curve between days that is drawn rather than measured.

## Context

- The website loads nothing from other sites, and its text is in the HTML when the page is built
  ([ADR 12](../adr/0012-website-static-bilingual-on-github-pages.md)).
- The first version of PR #30 showed the numbers as text and linked to the stats branch's charts for their
  history; `.github/scripts/repo_stats.py` draws those with straight segments or steps.
- A running total only grows, and a day's page views are never below zero, so a line that dips or goes below the
  bottom would show something that didn't happen.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| No picture on the card, with a link to the stats branch's charts | Nothing to draw | The trend is not on the page; the reader has to leave the site to see it |
| Straight segments between days, as the stats branch's charts are drawn | Simple; every corner is a measured value | Not the smooth line the cards' design asks for |
| A monotone cubic through the days, drawn by `site/build.py` as SVG | Smooth, and never above or below the values it joins, so a running total never seems to dip; no chart script; the numbers stay in the HTML | More code to keep: the slopes and curve pieces, and a test of them; the line between two days is interpolated |

## Decision

Chosen: the monotone cubic drawn at build time, because the cards' design calls for a smooth line, and a monotone
cubic is smooth without overshooting a value: a running total never seems to dip, and a day with no views never
seems below nothing. Drawn by `site/build.py`, each picture is SVG in the page, with a table of its numbers for
screen readers, and `site/site.js` only adds a tooltip that reads the nearest day from that table.

## Consequences

- No chart library is served or run, and no script draws anything when the page loads.
- `tests/test_site.py` checks that the handles of each curve piece stay between its ends, so the curve can't
  overshoot.
- The Arabic page mirrors the line, so time runs from the right.
- The curve between two days is a drawing, not a measurement; the tooltip and the table give the measured values.

## Artifacts

- [PR #30](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/30), merged as
  [9823ccf](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/9823ccf)
- `tangents()`, `curve()` and `spark()` in `site/build.py`, the figure cards in `site/DESIGN.md`, and
  `tests/test_site.py`
