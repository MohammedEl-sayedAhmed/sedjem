---
id: AgDR-0008
timestamp: 2026-10-30T16:21:45Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Pairing by a proof derived from a one-time code

> In the context of pairing two Sedjem computers with a one-time code over a network that may have another computer in between, facing a self-signed certificate that nothing can vouch for at the first connection, I decided that each side proves it knows an eight-character code with an HMAC under a key made by PBKDF2 from the code, the certificate the sending side sees and that side's nonce, the sending side first, to achieve one step for the user that also authenticates the server, accepting a code of letters and digits rather than digits alone and a scheme that isn't formally a password-authenticated key exchange.

## Context

- Issue #5 asks for pairing with a one-time code, limited to the local network, and the brief asked that pairing
  also authenticate the server: the code is shown with the certificate's fingerprint, or the check is derived from
  the code.
- The serving computer's certificate is self-signed (AgDR-0009), so at the first connection only the code, typed by
  the user, ties the two computers together.
- A computer in between can show its own certificate, pass on what it receives, and try every code against any
  value that depends on the code, offline, as fast as it can compute them.
- The cryptography package, which the build adds, has no PAKE such as SPAKE2, and writing one means writing elliptic
  curve arithmetic by hand.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Show the fingerprint with the code, for the user to compare | A standard check, nothing to design | 64 hex digits to compare by eye, which people skip |
| A digits-only code with an HMAC proof | Easy to type | About 27 bits: every code can be tried offline in seconds |
| A PAKE such as SPAKE2 | Formally sound with a short code | Not in the cryptography package; hand-written group arithmetic is easy to get wrong |
| An 8-character base32 code (40 bits), proofs both ways under PBKDF2(code; salt: the fingerprint and the sending side's nonce), the sending side first | One step for the user; a computer in between fails both ways and can't precompute anything | Letters as well as digits; not formally a PAKE |

## Decision

Chosen: the 8-character code with PBKDF2 proofs, because it authenticates the server in the same step as the code,
with nothing to compare by eye, and the parts it is made of are standard. The fingerprint in the key means a computer
in between, which shows another certificate, makes a key that neither side accepts; the sending side's nonce in the
salt, added after a security review, means no table of keys for all 2^40 codes can be made in advance; and the
serving side answers with its proof, and the token, only after the sending side's proof checks out.

## Consequences

- The user types eight letters and digits (Crockford's base 32: no I, L, O or U, and O read as 0), which work once and
  for ten minutes; five wrong tries void the code.
- The serving side makes the key again for each attempt, at most five times a code, so a stream of attempts costs it
  little.
- Its safety rests on 40 bits and PBKDF2 against a guess made within the seconds a pairing request waits, not on a
  proof as a PAKE would.

## Artifacts

- Commit [ddea8ea](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/ddea8ea),
  [PR #39](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/39)
- `app/remote.py` (pair_key, proof, Remote.pair and Remote.pair_request), and the pairing tests in
  `tests/test_remote.py`
- [Running jobs on another computer, How it is kept safe](../15-remote-runs.md#how-it-is-kept-safe)
