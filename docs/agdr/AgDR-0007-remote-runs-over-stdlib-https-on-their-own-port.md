---
id: AgDR-0007
timestamp: 2026-10-30T16:21:45Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Remote runs over HTTPS on a port of their own, to a typed address

> In the context of sending a recording to another computer of the user's to transcribe there, facing a choice between opening the app's own server, adding a WebSocket or gRPC library, or a separate server, and between finding computers on the network or typing their address, I decided on a separate HTTPS server written with Python's standard library, on a port of its own, polled by the sending computer, which reaches it at an address the user types to achieve a feature that adds no server dependency, leaves the app's own server on 127.0.0.1 as it was and broadcasts nothing, accepting a poll every second over a new TLS connection each time and an address to type when pairing.

## Context

- [Issue #5](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/5) asks for transcriptions run on another
  computer on the same network, such as a laptop with an NVIDIA card, paired with a one-time code.
- The app's server has no login: it listens on 127.0.0.1 only and refuses changes without its own header, which is
  what keeps other websites and other computers out ([the app, Privacy](../09-app.md#privacy)).
- The app's server and the MCP server are written with the standard library, so the desktop build doesn't grow
  with a web framework.
- The owner's computers are on a home network and on Tailscale, where each has a fixed address and a name.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Open the app's own server to the network | One server, one port | The page and its API, which have no login, would face the network |
| A WebSocket or gRPC library for the link | Progress pushed as it happens, no polling | A new dependency in every build, for one feature |
| A separate HTTPS server from the standard library, on its own port, polled by the sending computer | No new server dependency; the app's server stays on 127.0.0.1; plain JSON and a FLAC upload | A request every second while a job runs, each over a new TLS connection |
| Find computers by mDNS or a broadcast | Nothing to type | A second thing listening and announcing the computer on every network it joins |
| The user types the other computer's address or name | Nothing announces the computer; works the same over Tailscale | An address to type once, when pairing |

## Decision

Chosen: a separate HTTPS server from the standard library, on its own port (8766), reached at a typed address,
because it keeps the one part of Sedjem that faces the network small and apart from the app's own server, and
typing an address once is a small price next to a service that announces the computer on every network it joins.
Settings shows the serving computer's own private addresses, and Tailscale names work as typed.

## Consequences

- The app's server and its protections are unchanged, and the new server is off until the user turns it on
  (AgDR-0010).
- While a job runs, the sending computer asks for its state every second and for the lines so far every few
  seconds, in batches of about 256 KB, each request over a new TLS connection.
- The user types an address, or a Tailscale name, when pairing; nothing finds the other computer by itself.
- On Windows, the first time it listens, Windows asks whether Sedjem may use the network.

## Artifacts

- Commit [ddea8ea](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/ddea8ea),
  [PR #39](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/39)
- `app/remote.py` (Listener, Handler, Peer), `app/static/remote.js`, and `tests/test_remote.py`
- [Running jobs on another computer](../15-remote-runs.md)
