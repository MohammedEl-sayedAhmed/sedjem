---
status: Accepted
date: 2026-11-11
deciders: MohammedEl-sayedAhmed
---

# 17. The month so far, weekly, with headline numbers only

## Context

[ADR 10](0010-anonymous-usage-counts-through-aptabase.md) publishes the totals of the anonymous usage counts once a
month. [PR #86](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/86)
([issue #85](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/85)) added the month so far: during a month,
`tools/usage_totals.py --so-far` writes the totals of the month up to the day of the export into `docs/usage.json`,
about once a week, and the website shows them beside the newest finished month
([usage counts](../13-usage-counts.md#public-totals)). ADR 10 does not mention the month so far.

Each weekly copy stays in the git history, and a reader can subtract two copies of the same month. When one install
starts between them, the difference of each breakdown shows that install's values: its country, region, system,
graphics card, memory and version of Sedjem. The rule that adds a value with fewer than three installs to "other"
holds inside one copy, not across two ([issue #87](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/87)).

The first copy, November 2026 to 9 November, went in with every breakdown, in commit
[5373008](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/5373008). It had 12 active installs and 31 website
visitors. No later commit can remove it from the history, and the finished November can be subtracted from it in the
same way ([issue #89](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/89)).

## Decision

From [PR #88](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/88) and the pull request that closes issue #89:

- The month so far is published about once a week. The run for that month, once it is finished, removes it.
- The month so far holds headline numbers only. For the app these are the active and new installs, the number of
  countries, the transcriptions and the hours of audio. For the website they are the page views, the visitors, the
  number of countries, the download clicks and the visits by country and by day. It holds no breakdown.
- A finished month keeps every breakdown, as ADR 10 says, unless the git history holds a copy of it with every
  breakdown. Then each part of it keeps its breakdowns only when at least three active installs (the app's part) or
  three visitors (the website's part) came after that copy. With fewer, the part has its headline numbers only, marked
  `headline_only`. `FULL_COPIES` in `tools/usage_totals.py` lists these copies. November 2026 is the only one.

Two other ways for November were considered. November could have headline numbers only whatever the count, but then
it loses its breakdowns even when enough installs came after the copy. Or November could be published as usual, with
a note in the [privacy policy](../privacy.md) that it can be compared with the old copy, but that breaks the promise
that no one install can be picked out.

## Consequences

- Two copies of one month differ in headline numbers only. The finished November and its old copy differ by at least
  three installs, or in headline numbers only.
- The check counts the installs that came after the copy as one group. It does not check each value. The difference of
  a breakdown can still show that one or two of those installs have a value: a graphics card, or a country, region,
  system or version that the finished month names. It cannot show which values belong to the same install, and it
  names no value that the finished month adds to "other". One month's hardware counts are not folded either.
- The website shows the month so far's headline numbers and visits, and the census of the newest finished month. A
  finished month with headline numbers only has no breakdown, so its census holds the website's visits at most.
- November 2026 may have no breakdowns. Then the table in the usage counts page says "not published" for its share
  of hosted runs.
- A copy of a month so far with every breakdown must not go into the repository again. If one does, it has to be added
  to `FULL_COPIES`.
- ADR 10 stays accepted for what it decides. This record adds the month so far to it.
