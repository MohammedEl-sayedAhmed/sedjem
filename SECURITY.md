# Security

## Report a vulnerability

Report a security problem privately, not in a public issue: open
[Report a vulnerability](https://github.com/MohammedEl-sayedAhmed/sedjem/security/advisories/new) on the Security tab.
GitHub keeps the report between you and the maintainer until its security advisory is published, with the fix.

Say what an attacker could do, and how to make it happen: the version (*Settings → About*), the system, and the steps
or a small proof. Leave out your own recordings, transcripts and API keys; a made-up file shows the problem as well.

## What happens next

- You get a first answer within a week.
- If the problem can't be confirmed, or isn't a vulnerability, you get the reason and the report is closed.
- A confirmed problem is fixed in a new release. Its security advisory says what was wrong, which versions it affects
  and how to update, and thanks you by name if you want that.
- Please keep the details to yourself until that release is out. If a fix takes longer than 90 days, the date of
  the disclosure is agreed with you.

## Versions that get fixes

Only the newest release gets security fixes. *Settings → Updates* checks for it, and *Update now* installs it
([updates](docs/12-updates.md)). The nightly previews get the fix in the next preview, and a setup from source gets
it from main.

## What counts

Any part of Sedjem that someone else could reach or feed, for example:

- the local server and its API, which answer only on this computer (127.0.0.1);
- the MCP server for assistants ([assistants](docs/11-assistants.md));
- *Run jobs for other computers* and the pairing for it ([remote runs](docs/15-remote-runs.md));
- what Sedjem downloads and reads: the models, the updates, and the videos of YouTube links;
- the desktop builds, the installer and the website.

A problem in a hosted service itself, such as ElevenLabs or OpenAI, goes to that service. An API key that you
pasted into a public place is not a vulnerability in Sedjem: revoke it with the service.
