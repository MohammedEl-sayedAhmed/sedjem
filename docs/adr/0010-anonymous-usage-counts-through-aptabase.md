---
status: Accepted
date: 2026-10-29
deciders: MohammedEl-sayedAhmed
---

# 10. Anonymous usage counts through Aptabase

## Context

Without any counts, the project can't know how many installs use Sedjem, in which countries, on which versions and
systems, or which models and features they use ([usage counts](../13-usage-counts.md)). Sedjem keeps recordings and
transcripts on the computer ([ADR 2](0002-recordings-and-transcripts-stay-on-the-computer.md)), so any count has to
leave all of that out.

## Decision

From [52329dc](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/52329dc)
([PR #23](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/23), extended in
[PR #24](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/24)):

- Sedjem sends three kinds of event to Aptabase, in its EU region: `install` once for each data folder,
  `active_month` once a calendar month, and `week_summary` once for each week of use. That is about six events a
  month for an install used every week.
- Every value is a whole number or one of a few fixed values. No recording, transcript, title, name, file name,
  path, link, key, error message, anything an assistant asked, or lasting identifier is sent, and every event is
  checked against the documented tables on its way out and dropped if it holds anything else.
- The counts are on by default, but nothing is counted or sent before the first-start dialog (or the Windows
  installer's Privacy page) has said so and offered the switch. The switch in Settings → About, `DO_NOT_TRACK=1` or
  `SEDJEM_NO_USAGE=1` turns them off, and turning them off deletes what was kept.
- Once a month the events are exported, and `tools/usage_totals.py` makes public totals of them, with countries
  and systems below three active installs folded into "other".
- A copy without an app key counts and sends nothing, and the tests and the workflows never send.

## Consequences

- The project learns how Sedjem is used, and anyone can read the same totals.
- Aptabase sees each computer's IP address; by its terms it uses it only in memory, for the country and region and
  a daily pseudonymous hash, and doesn't store it.
- Every new event field has to be added to the documented tables, or the check on the way out drops the event.
  The README, the app's notes and the [privacy policy](../privacy.md) say the same as the usage counts page.
- The totals are made by hand from a monthly export, since Aptabase has no other way to read the counts back.
- The website's page counts came later on the same service ([ADR 12](0012-website-static-bilingual-on-github-pages.md)).
