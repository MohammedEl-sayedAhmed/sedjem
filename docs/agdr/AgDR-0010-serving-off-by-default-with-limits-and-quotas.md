---
id: AgDR-0010
timestamp: 2026-10-30T16:21:45Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Serving off by default, private addresses only, with limits and quotas

> In the context of the port that takes recordings from other computers, facing networks that are private in their addresses but not always trusted, and a paired computer that could send more than one computer should hold, I decided that the port opens only when the user turns serving on, takes connections only from private ranges and Tailscale's, closes the rest before reading them, and caps connections, the recordings it takes and each computer's jobs and disk space to achieve a service that is closed unless asked for and can't be made to run out of memory or disk, accepting that the address filter alone doesn't make a network safe and that a computer can use only so much of the other.

## Context

- The brief asked for serving off by default, connections only from private addresses (RFC 1918, link-local, IPv6
  unique-local and Tailscale's 100.64.0.0/10), refused before any parsing, and uploads with a size limit.
- A security review then found that a few hundred KB of FLAC silence decodes to thousands of hours, that cancelled
  and uncollected jobs counted against no limit, and that one address could hold every connection.
- Café and hotel Wi-Fi, Docker, virtual machine and WSL networks, and some mobile carriers use private addresses
  too; carriers share 100.64.0.0/10 with Tailscale.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Listen whenever the app runs | Nothing to turn on | An open port on every network, for a feature most people don't use |
| Any address, with the token as the only check | Works across any route | Strangers on the internet could knock on it |
| Private ranges only, checked before TLS, with the code, token and pinned certificate as the real protection | Nothing outside a local network gets a TLS handshake | Private isn't trusted: shared Wi-Fi and virtual networks pass the filter |
| Limit only the jobs waiting or running | Simple | Cancelled and uncollected jobs, and uploads in flight, fill the disk unchecked |
| A quota per computer over all its jobs, with uploads reserved, and limits on what is decoded | The disk and memory stay bounded | A computer that lost touch can hold its quota for up to a day |

## Decision

Chosen: off by default, private ranges checked before TLS, and limits and quotas, because each closes a way in or a
way to exhaust the computer without costing the owner's own use. The docs say that the filter only narrows who can
knock, that the code, the token and the pinned certificate are what protect the port, and to turn serving off on a
network the user doesn't trust.

## Consequences

- The port (8766) is closed until *Run jobs for other computers* is turned on. Turning it off closes it and deletes
  the recordings sent there.
- Any other address, loopback included, is closed before its first byte is read. At most 32 connections are open at
  once, four from one address, and ten failed codes or tokens in ten minutes block an address for the rest of them.
- A recording is taken only as FLAC, up to 2 GB, 48 kHz, two channels and 48 hours, checked in its header and again
  while it is decoded, which stops at 48 hours. Recordings longer than that can't be sent.
- Each computer may have ten jobs and 6 GB there, whatever their state and including uploads in flight, and an
  upload stops if the disk would keep less than 1 GB.
- Errors go back in fixed words, so no path of the serving computer leaves it.
- A public IPv6 address on the same network is refused, like any address outside the ranges.

## Artifacts

- Commit [ddea8ea](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/ddea8ea), and the security review's fixes
  in [PR #39](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/39)
- `app/remote.py` (private, Listener, Remote.accept and Remote.reserve, streaminfo_problem), the limits in
  `app/jobs.py` convert, and the Limits and review tests in `tests/test_remote.py`
- [Running jobs on another computer, How it is kept safe](../15-remote-runs.md#how-it-is-kept-safe)
