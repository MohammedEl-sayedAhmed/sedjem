---
id: AgDR-0018
timestamp: 2026-11-12T14:18:40Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Release days from the list of releases, and a deleted release keeps its last count

> In the context of the website's downloads chart, facing releases dated a day late and a running total that fell when a preview was deleted, I decided to read each release's published_at from the list of releases that the Pages workflow downloads, and to keep a deleted release's last count in the site, the README's badge and the stats branch's charts, to achieve a chart that marks each release on its day and a total that never falls, accepting one more API call for each Pages run and a total higher than GitHub's own sum.

## Context

- The site dated each release by the first day the daily stats counted it
  ([issue #82](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/82)). The stats run in the morning UTC, so a
  release published later in the day was dated the next day: 0.3.2, published on 30 October at 18:51 UTC, showed as
  "out on 31 October".
- `preview.yml` deletes old previews when it makes a new one. A deleted release leaves GitHub's numbers, so the
  running total fell, from 66 on 6 November to 65 on 7 November. This happens again with each new preview.
- The stats keep a snapshot of every release file's count each day in `downloads.csv` on the `stats` branch, so a
  deleted file's last count is still there.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| The Pages workflow downloads the list of releases, and `site/build.py` reads published_at | One API call, which counts no download; the stats files do not change; the dates are GitHub's own | One more call in each Pages run |
| The stats job records each release's published_at in `downloads.csv` | No extra call when the site is built | The stats file changes its columns, and the earlier rows must be filled in |
| Keep a deleted release's last count in the site, the badge and the stats branch's charts | The total never falls, and the numbers agree everywhere | The total is higher than the sum of GitHub's current numbers; two scripts change |
| Keep it on the site only | Only `site/build.py` changes | The site's total and the README's badge differ |
| Explain the dip under the chart | The smallest change | The chart still falls after each new preview |

## Decision

Chosen: the list of releases in the Pages workflow, and a deleted release's last count kept everywhere, because
GitHub's published_at is the release's real day and the stats files need not change, and because a total that never
falls must be the same on the site, the badge and the stats branch. The user chose both options when the ticket was
started.

- `pages.yml` downloads `repos/OWNER/NAME/releases` with `--paginate --slurp` into the release folder as
  `releases.json`. A failed call leaves a warning and never stops the build.
- `published()` in `site/build.py` reads published_at in UTC for each release that is not a preview or a draft,
  from `releases.json` and from `release.json`. Without `releases.json`, `release.json` still dates the latest
  release.
- A release's ring and its row in the table are on its published day, when the stats have that day. "Out on" is its
  published day in any case. Past 35 days, the line is drawn by the week, and it keeps each release's day.
- `site/build.py` and `.github/scripts/repo_stats.py` both carry each file's newest count forward from day to day,
  so a file that leaves the snapshots keeps its last count. The stats branch's summary lists such a file with
  "(deleted)".

## Consequences

- The site's total, the README's badge and the stats branch's chart agree, and none of them falls. On 12 November
  2026 they show 92 downloads, where GitHub's current numbers add up to 85: the other 7 came to previews that were
  deleted.
- `downloads.csv` does not change. It stays GitHub's snapshot of each day.
- A release published on a day the stats have no row for gets no ring, but "out on" still shows its day.

## Artifacts

- `.github/workflows/pages.yml`, the step "Download the list of releases (through the API)"
- `site/build.py`, `published()`, `github()` and `weekly()`
- `.github/scripts/repo_stats.py`, `kept()` and `app_total()`
- `site/DESIGN.md`, the downloads chart
- `tests/test_site.py`, `tests/site_support.py` and `tests/test_repo_stats.py`
