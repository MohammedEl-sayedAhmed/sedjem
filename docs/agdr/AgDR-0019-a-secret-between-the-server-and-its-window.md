---
id: AgDR-0019
timestamp: 2026-11-18T13:06:26Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# A secret between the local server and its own window

> In the context of the app's local server, facing programs that other user accounts run on the same computer and that could use its API, or wait on its port, I decided to require on every /api request a secret that the server makes new at each start, on a new free port, and keeps in a file only the user can read, sent by the page as a header from its tab's sessionStorage, given to the desktop window through its bridge and to a browser window through a local page that posts it, opened with a browser profile of the app's own, with a per-job signature for audio and video and the page's preferences kept in the data folder, and to trust a server on a port only after it proves that it knows the secret for that port, to achieve an API that only the user's own window can use, accepting a new window after each start of the app, no installed web app, and a header for scripts and tests.

## Context

- The server listens on 127.0.0.1 only and checks the Host and Origin headers and the `X-Sedjem` header, which stops
  other websites. It had no login, so any program on the computer that can reach a loopback port could use the API,
  including one that another user account runs on a shared computer.
- What reaches the API: the transcripts, the recordings, starting a transcription of a local file by its path, the
  settings and the hosted services' keys.
- The page needs to be let in from the desktop window (pywebview) and from a browser window that `./app.sh` or the
  desktop app opens. Scripts and the tests call the API too.
- On Linux and Windows, another account can read the command lines of running programs, so a secret in the address
  that the app gives the browser would be visible for a moment.
- A cookie for 127.0.0.1 goes to every server on 127.0.0.1, whatever its port, so a server that another account runs
  on another port would get it if the user opened one of its pages.
- localStorage belongs to one origin, port included, but it stays after the app stops; a program that takes the
  port later serves pages from the same origin and could read it.
- A program on the port can do more than read storage. While Sedjem isn't running there, it can serve the browser one
  page, from a tab, a bookmark or a restored session, and leave a service worker for that origin. The worker stays in
  the browser profile and sees the next request to the origin, the login included, and every API call after it. A
  fix inside the origin, such as Clear-Site-Data, comes after the worker has seen the login.
- An address with a long-lived secret stays in browser history and in the download history of exported files.
- Another account could also take the app's port before it starts and answer as if it were Sedjem; a second start
  would then send it the secret.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| A new secret and a new free port at each start, in files only the user can read; the page keeps the secret in its tab's sessionStorage and sends it as a header; a per-job signature for media; the window gets it from its bridge, a browser window from a local page that posts it, with the app's own browser profile; the page's preferences in the data folder | Never in a command line, an address, a cookie or storage that outlives the tab; worthless once the copy that made it stops; a program can't know the next port, so it can't wait there | After each start, including an update, the app opens a new window, and the old one says so; exports are fetched and saved by the page; no installed web app |
| The same on the fixed port 8765, with the app's own browser profile | The page's preferences stay in the browser | A user without a Chromium-family browser gets the default browser, where a service worker left on 8765 sees the login |
| The same with a new port, and the preferences left in localStorage | Less code | The theme, the library's order and the update notices start again at every start |
| The same, with a cookie after the login | Plain links and media work as before | The cookie goes to every server on 127.0.0.1, so another account's server could collect it |
| The same secret across restarts, in localStorage | A tab survives an update restart | A program that takes the port after the app stops reads it from the same origin, and it stays valid |
| A one-time code in the address the app opens | Simple | The code is in the browser's command line, which other accounts can read before the browser uses it |
| A Unix socket or a named pipe with the user's permissions | The operating system checks the user | Browsers can't use them; the page would need another server in front |
| A password the user types | Clear to the user | One more thing to set and remember, for an app that runs on the user's own computer |
| Accept the risk and document it | No change | Leaves transcripts and recordings open to other accounts on shared computers |

## Decision

Chosen: a new secret and a new free port at each start, the secret kept in the tab's sessionStorage and sent as a
header, because it never appears where another account can read it, it is never sent to another port, it is
worthless once the copy that made it stops, and no program can wait on the port that the next start takes. The user
approved the plan and chose the port, the browser profile and the preferences in the data folder; the first draft's
cookie and the second draft's localStorage and lasting secret were replaced after security reviews of each.

- `app/session.py` makes 64 random hex digits once the server has its port, and writes them to
  `<storage>/session.key` and the port to `<storage>/port`, both with mode 0600. Reading the key never changes it,
  so a second start that finds the port taken leaves the copy that is running alone.
- `[server] port` is 0 by default: a new free port at each start, an update's restart included. A port the system
  hands out is never 8765, so what earlier versions left on that port can't reach a start. `--port N`, or a
  `[server] port` other than 0, fixes the port again, and with it the risk of a service worker left on it.
- On Windows the server opens its port with SO_EXCLUSIVEADDRUSE and without SO_REUSEADDR, which would let another
  program, another account's included, bind the same port and take some of its connections.
- Every `/api` request except `GET /api/ping` and `POST /api/login` needs the secret in the `X-Sedjem-Token` header,
  compared in constant time. The Host, Origin and `X-Sedjem` checks stay.
- Audio and video, which a media element loads by its address, take `?sig=` instead: an HMAC of the job's ID, keyed
  with the secret, good for that job's `audio` and `video` routes on GET and HEAD only. The job's JSON carries it.
- Exports are fetched by the page with the header and saved from memory, with the save picker where the browser has
  one.
- `/api/login` takes the secret as a form from `<storage>/open-sedjem.html` (Origin `null`, the only route that
  accepts it) and answers with a page from the app's own origin that stores it in sessionStorage and goes to the
  app. The desktop window's page asks its bridge (`Api.session`) instead.
- The app opens `open-sedjem.html`, mode 0600, instead of the address, also after an update restart. `./app.sh` and
  the desktop app open it in a window with a browser profile of the app's own (`<storage>/browser-profile`) when they
  find Edge, Chrome, Chromium or Brave, else in the default browser. The app itself opens in that profile only ports
  that it has checked.
- Before a second start trusts a copy that is already running, it reads the port from `<storage>/port`, sends
  `/api/ping` a random nonce and checks the answer: an HMAC of the nonce and the port, keyed with the secret. A program
  that only took the port, or that passes the nonce on to Sedjem on another port, can't answer it, so it is never
  sent the secret or shown a window. An answer that isn't a proof counts as no answer.
- The page's preferences (the theme, the library's order, the video size and the update notices) are kept in
  `<storage>/page.json` through `/api/prefs`, since localStorage is new with each port; the server puts the theme
  into the page, so it doesn't flash.
- The manifest and the service worker are gone: an installed web app would open the address without the login page.
- After Update now, the page says that Sedjem opens again in a new window and that this one can be closed; it no
  longer reloads, since the new copy is on another port. It keeps asking the copy it came from until that one quits,
  so an update whose new copy stops at once still shows as failed.
- A page without the secret says how to open Sedjem instead.

## Consequences

- Another account on the same computer gets 401 from every `/api` route. A test walks the route table to check each
  one, so a new route can't be left open by mistake.
- Each start of the app, an update's included, opens a new window; a window from before says that Sedjem has
  restarted.
- A window that the user opens by hand at the address, without the page the app opens, says how to open Sedjem.
- The address changes at every start, so a bookmark of it doesn't work; the app prints the login page's path.
- Scripts that used the API with `X-Sedjem: 1` alone now also send `X-Sedjem-Token`, read from `session.key` while
  the app runs, to the port in `port`.
- On Windows the file mode is ignored; the data folder's own permissions keep `session.key` private, as they keep
  `secrets.json` and the transcripts. That is the user's profile for the installed app, and the folder the user chose
  when `[storage] dir` or `SEDJEM_HOME` points elsewhere.
- The preferences that the page kept in the browser before this change start from their defaults once. A web app
  installed from the page by an earlier version opens the old address without the login page, so it is best removed.
- In the default browser, the new port is the only defence against a service worker: a program that gets the
  browser to load pages from many ports on 127.0.0.1 while Sedjem isn't on them could leave one on a port that a
  later start takes, though it can't know which.
- Two starts in the same moment can both miss each other and both run a server on one data folder, as the desktop
  app could before.
- Browsers packaged as Snaps can't read files in hidden folders of the home folder, such as the Linux build's data
  folder `~/.local/share/sedjem`, so there the login page may not open in one, and a Snap Chromium that the app finds
  first gets no fallback to another browser; the desktop window opens it.
- A Content-Security-Policy, when it comes, has to allow the landing page's small inline script, or move it to a file.
