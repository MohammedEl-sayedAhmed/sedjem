# Running jobs on another computer

Sedjem can send a recording to another computer of yours on the same network, which transcribes it and sends the
transcript back. A laptop without a graphics card can use one with an NVIDIA card this way, for example. The other
computer runs the job with its own models, its own downloads and its own graphics card, through its usual queue; the
transcript is kept on the computer that sent it, as a normal transcription
([issue #5](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/5)).

Both computers need this version of Sedjem or a later one. YouTube links and courses always run on the computer they
are added on, in this first version.

## Pairing two computers

On the computer that will run the jobs:

1. Open *Settings → Other computers* and turn on *Run jobs for other computers*. It is off until you turn it on.
2. Click *Show a pairing code*. The tab shows a code of eight letters and digits, such as `7KQ2-M9XD`, and the
   addresses of this computer on its networks.

On the computer that will send them:

1. Open *Settings → Other computers*, type one of those addresses and the code, and click *Pair*.

The code works once and for ten minutes. Five wrong tries void it, and a new one has to be shown. Each computer then
lists the other by its name. The name is the computer's own, or `[remote] name` in the data folder's `config.toml`.

On Windows, the first time *Run jobs for other computers* is turned on, Windows asks whether Sedjem may use the
network. Allow it on private networks.

## Sending a recording

Once a computer is paired, the *New transcription* form has a *Run on* choice: *This computer*, or the paired computer
by name. Picking it asks that computer which models it has installed, and the form lists only those, with how long
they should take there. The first time a recording goes to a computer, the form says that it will be sent to that
computer on your network and asks for a tick, as it does before an upload to a hosted service. After that it says so
without asking again. Speaker labels, the language and the vocabulary work as they do on the computer itself.

While it runs, the job's page shows how far it is on the other computer, and the transcript as it grows. Cancel works
as for a job run here, and stops it there too. When it is done, the transcript is stored here as a normal
transcription, and its details say where it ran, such as *Ran on Office-PC, graphics card: NVIDIA GeForce RTX
3060 (CUDA)*, with that computer's processor, memory and system.

If the other computer doesn't answer when the job starts (it is off, asleep or on another network, or Sedjem isn't
running there with *Run jobs for other computers* on), the job runs on this computer instead, when the same model is
installed here, and its page says so. Without the model here, the job fails and says why. If the other computer stops
answering after it has taken the job, Sedjem keeps trying for a minute and then fails the job, with *Run here
instead* and *Try again* on its page. It never switches a job to this computer halfway through by itself.

## What is sent, and what stays

- The sending computer sends the recording as 16 kHz mono FLAC, the audio Sedjem makes from any recording, with the
  model, the number of speakers, the language and the vocabulary. It never sends the original file, a video, the
  title, a path or anything else from its library.
- The other computer sends back the transcript and the facts of the run: the device, the model's file, the memory it
  used, the version of Sedjem and the computer's maker, model, processor, memory, system and graphics cards.
- The recording goes only to the computer you paired with, and only over your local network: both sides refuse any
  address that isn't a private one.
- On the computer that runs it, the recording and its job are kept apart from its own library, and deleted once the
  transcript has been collected, or a day after the job ended if it never is. They are also deleted when *Run jobs
  for other computers* is turned off, or when the computer that sent them is removed there.
- Model downloads stay on the computer that runs the models.

## How it is kept safe

- *Run jobs for other computers* is off by default. Only then does Sedjem listen on the network, on a port of its own
  (8766, or `[remote] port` in `config.toml`). The app's own page and API stay on 127.0.0.1 as before.
- That port answers only private addresses: home and office networks (10.0.0.0/8, 172.16.0.0/12 and 192.168.0.0/16),
  link-local addresses, IPv6 unique-local addresses (fc00::/7) and Tailscale's 100.64.0.0/10. A connection from any
  other address is closed before anything on it is read. Private addresses are not the same as trusted ones, though:
  café and hotel Wi-Fi, Docker, virtual machine and WSL networks, and some mobile carriers (which share 100.64.0.0/10
  with Tailscale) use them too. What protects the port is the pairing code, the token and the pinned certificate below.
  Turn *Run jobs for other computers* off on a network you don't trust.
- Everything between the two computers is encrypted with TLS 1.3. The computer that runs jobs makes its own
  certificate the first time it is turned on. Pairing checks it: the code is never sent; each side proves it knows
  the code with a key made from the code, the certificate it sees and a random number new to each attempt, so a
  computer in between, which would show another certificate, can't pass, and nothing can be worked out in advance. The sending computer then keeps the certificate's fingerprint and checks it every
  time, before it sends anything. The fingerprint is shown in *Settings → Other computers* on the computer that runs
  jobs.
- Pairing gives the sending computer a token for itself alone. It is kept with the API keys, in `secrets.json`, and
  never shown. The other computer keeps only a hash of it, and checks it on every request before doing anything.
  Either computer can remove the pairing in *Settings → Other computers*; the token then stops working.
- Wrong codes and wrong tokens count against the address they came from: after ten in ten minutes, it is refused for
  the rest of those ten minutes.
- The computer that runs jobs only runs what Sedjem itself would: a transcription with one of its installed models. It
  takes the recording only as FLAC, up to 2 GB, 48 kHz, two channels and 48 hours, which it checks in the file's header
  and again as it decodes it. Each computer may have at most ten jobs there, whatever their state, and 6 GB of space,
  uploads included, and an upload stops if the disk gets close to full. It reads no path it is sent, gives each
  computer only its own jobs' state and transcript, and says what went wrong in fixed words, which never name its
  folders. It takes at most four connections at once from one address.
- The sending computer treats what comes back as data: the transcript's lines and the facts are checked and cut to
  size before they are kept, and nothing in them is run.

To stop running jobs for other computers, turn *Run jobs for other computers* off. To stop sending to one, remove it
from the list in *Settings → Other computers*.

## Computers on different networks: Tailscale

The two computers have to reach each other on a private network. At home or in an office they usually do. When they
are in different places, [Tailscale](https://tailscale.com) is the easiest way: install it on both and sign in to the
same account, and each computer gets an address in 100.64.0.0/10 (shown in *Settings → Other computers*) and a name
such as `office-pc.tail1234.ts.net` that works from the other one. Type that address or name when pairing. The
recording then travels encrypted twice, by Tailscale and by Sedjem, and still only between your two computers.

## Limits

- One job at a time is sent from a computer; the others wait for it.
- A recording longer than 48 hours can't be sent to another computer.
- A job sent to another computer can be run again there (*Try again*) or here, but *Run again with* lists only this
  computer's models.
- If the app on the sending computer is closed while a job runs elsewhere, the job is marked interrupted; the other
  computer deletes it a day later.
- If the computer that runs jobs is set up again, it makes a new certificate, and the sending computer refuses it until
  the two are paired again.

## For scripts

The app's API (127.0.0.1, with the `X-Sedjem: 1` header for changes) has these calls; see also
[the app's API](09-app.md#api-for-scripts).

| Call | What |
|---|---|
| `POST /api/settings` with `{"remote_serve": true}` | *Run jobs for other computers* on (`false` turns it off) |
| `GET /api/remote` | both sides: whether this computer serves, its addresses, the open code and the computers that can send here, and the computers this one can send to |
| `POST /api/remote/code`, `DELETE /api/remote/code` | show a new pairing code, or close it |
| `POST /api/remote/pair` with `{"address": …, "code": …}` | pair with the computer at that address |
| `DELETE /api/remote/computers/<id>`, `DELETE /api/remote/clients/<id>` | unpair from a computer this one sends to, or remove one that sends here |
| `GET /api/remote/computers/<id>` | what a paired computer says about itself now: its models, speaker labels, computer and queue |
| `POST /api/jobs` with `run_on` and `confirm_send` | a transcription on a paired computer; `confirm_send=1` is needed the first time for each computer. `POST /api/jobs/<id>/rerun` takes `run_on` too |

The code is in `app/remote.py`, the interface in `app/static/remote.js`, and the tests in `tests/test_remote.py` and
`tests/test_ui_remote.py`.
