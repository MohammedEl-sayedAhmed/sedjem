---
id: AgDR-0016
timestamp: 2026-11-07T15:40:00Z
agent: claude-code
model: claude-sonnet-5-5
trigger: user-prompt
status: executed
---

# A weekly OpenSSF Scorecard run that reports to the Security tab

> In the context of a pipeline whose supply-chain rules are checked only by Sedjem's own tests, facing no outside measure of whether they match what the OpenSSF considers good practice, I decided to run the OpenSSF Scorecard action every week and on changes to the branch protection, with its findings in the Security tab and its result published to scorecard.dev, to achieve an independent score and a list of gaps, accepting findings that cannot all be fixed and a result that goes to a public service.

## Context

- `tests/test_pipeline.py` pins what the maintainer decided: pinned actions, the fewest permissions, hashed locks. It
  cannot find a rule nobody thought of, and it does not look at settings such as branch protection.
- Clipman runs `ossf/scorecard-action` in `.github/workflows/scorecard.yml`, weekly and on pushes to main and changes to
  the branch protection rule, with results in the Security tab.
- The action, when it publishes its result, accepts only a narrow workflow: top-level permissions that are empty or
  read-only, the permissions of the job inside the job, and a short list of steps (checkout, the action, an artifact
  upload and the SARIF upload). Sedjem is a public repository, which publishing needs.
- GitHub's default token cannot read all of the branch protection settings, so the Scorecard check for them may come
  back unscored without a personal token.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| A weekly workflow with the action, publishing to scorecard.dev and uploading SARIF to the Security tab | Independent score; findings next to CodeQL's; reruns when the branch protection changes; nothing to install or maintain but one pinned action | Some checks cannot be met by this project (signed releases, fuzzing, a best-practices badge); the repository's public metadata goes to scorecard.dev |
| The same without publishing | Nothing leaves GitHub | No badge or public score; the permission `id-token: write` is not needed, but the project loses the one visible result |
| Running the Scorecard command-line program by hand now and then | No workflow | Downloads a program that the pipeline would have to check against a hash; nobody remembers to run it; no history |
| Nothing: rely on `tests/test_pipeline.py` | Less to read | Only the rules already known are checked |

## Decision

Chosen: the weekly workflow with publishing, because the score and the Security tab entries are an outside check at
the price of one pinned action. `.github/workflows/scorecard.yml` runs on Mondays, on every push to main and when the
branch protection rules change, with `permissions: {}` at the top and, in the one job, `contents: read`,
`actions: read`, `security-events: write` and `id-token: write` (publishing). It uses no other action than the ones
the publishing rules allow. The action, the artifact upload and the SARIF upload are pinned to
commits like every other action, and `tests/test_pipeline.py` checks the workflow.

## Consequences

- The Security tab gets a second source of findings. They are not part of the CodeQL ratchet and block nothing.
- Findings that this project cannot or will not fix stay open. They are read as a list of choices, not as a task
  list.
- The result goes to the public Scorecard service, with the repository's public settings and workflow files. No
  secret and no data of a Sedjem user is in it.
- Without a personal token the branch protection check may be unscored. Adding a token would be a new secret and a
  new decision.
- The action's version is kept current by Dependabot like the other actions.

## Artifacts

- `.github/workflows/scorecard.yml`
- `tests/test_pipeline.py`
- `docs/14-pipeline.md`, Security
