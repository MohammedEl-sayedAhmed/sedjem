---
status: Accepted
date: 2026-09-25
deciders: MohammedEl-sayedAhmed
---

# 4. AGPL-3.0-only, with commercial licences from the author

## Context

The project was first published under the MIT licence
([970805a](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/970805a)), earlier the same day. MIT lets anyone
use the code in a closed product or a hosted service without publishing their changes.

## Decision

From [bfa19ca](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/bfa19ca), Sedjem is licensed under the GNU
Affero General Public License, version 3 only ([LICENSE](../../LICENSE)), with these terms in
[NOTICE](../../NOTICE):

- As an additional term under section 7(b), any copy or modified version keeps the attribution "Sedjem by
  Mohammed El-sayed Ahmed", with a link to the repository, in the app's About section and in its README.
- Commercial licences are available from the author, for use without the AGPL's conditions, for example in a
  closed-source product or a hosted service whose changes are not published.
- Versions published up to 25 September 2026 stay under the MIT licence.
- The models Sedjem downloads and the libraries it bundles keep their own licences, listed in the README.

The installer shows the licence and the publisher, and the build carries LICENSE and NOTICE.

## Consequences

- Anyone who distributes a modified Sedjem, or runs one as a service for others, has to publish its source under
  the same licence and keep the author credit.
- The author can license Sedjem commercially as well, which the AGPL alone wouldn't allow for closed use.
- Offering commercial licences beside the AGPL rules out programmes that sign only for projects without
  commercial dual-licensing ([ADR 11](0011-windows-no-free-signing-ship-through-winget.md)).
- Copies of the MIT versions published before the change remain under MIT.
