---
id: AgDR-0009
timestamp: 2026-10-30T16:21:45Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# A pinned certificate, and a token for each computer

> In the context of every connection between a paired computer and the one that runs its jobs, facing a self-signed certificate that no authority signs and a need to tell which paired computer is asking, I decided that the sending side compares the certificate's SHA-256 fingerprint, pinned at pairing, before it sends anything, with Python's own certificate check turned off, and that each paired computer carries a token of its own, kept hashed on the serving side, to achieve encrypted links to the right computer and access that either side can revoke, accepting a new pairing after the serving computer is set up again and a token that works from any private address until it is revoked.

## Context

- The brief asked for TLS with a self-signed certificate made once by the serving side, pinned at pairing, and for
  per-computer tokens that are revocable on either side and compared in constant time.
- Python's ssl module can trust a given certificate as its only authority, but how OpenSSL treats a self-signed
  certificate that isn't marked as an authority has changed between versions.
- Each request must say which paired computer it comes from, so that each sees only its own jobs.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| A certificate signed by an authority | The usual check | No authority signs a computer on a home network |
| The pinned certificate as the only trusted authority | OpenSSL does the check | Depends on how each OpenSSL version treats a self-signed certificate |
| Certificate checks off, the pinned SHA-256 fingerprint compared as soon as the connection is made, before anything is sent | Works the same on every version; nothing leaves before the check | Code scanners flag the turned-off check; a mistake there would turn pinning off |
| Client certificates (mutual TLS) to say who is asking | Nothing secret in the requests | A certificate and key to make and keep on every sending computer |
| A token for each paired computer, kept hashed on the serving side and in secrets.json on the sending side | Simple; revoked by deleting one entry | A copied token works until it is revoked |

## Decision

Chosen: the fingerprint compared before anything is sent, and a token for each computer, because both behave the
same everywhere and are a few lines each. A missing fingerprint never means "don't check": only the pairing call
connects without one, and says so.

## Consequences

- Each request carries a 256-bit token. The serving side keeps only its SHA-256 and compares every stored one in
  constant time, before it does anything else.
- Removing a computer on either side stops its token at once, and on the serving side stops and deletes its jobs.
- A computer whose certificate was made again, after it was set up again, is refused until the two are paired again.
- Someone who copies secrets.json from the sending computer can use its token from any private address until it is
  revoked.

## Artifacts

- Commit [ddea8ea](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/ddea8ea),
  [PR #39](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/39)
- `app/remote.py` (certificate, Connection, Remote.client_for), and the token and certificate tests in
  `tests/test_remote.py`
- [Running jobs on another computer, How it is kept safe](../15-remote-runs.md#how-it-is-kept-safe)
