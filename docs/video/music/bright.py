"""The bright version of the Sedjem video track: synthesised from scratch (numpy only), free to publish.

120 BPM, 4/4: a beat is 0.5 s and a bar 2 s. C major, I-V-vi-IV (C, G/B, Am, F), one chord per bar.
One section per scene:
  0-6 s    hook: soft pad and a sparse pluck, the filter opening, a riser into the drop
  6-10 s   logo: impact, a gentle kick and long bass notes
  10-18 s  Step 1: claps, hats and the bouncing bass
  18-26 s  Step 2: an eighth-note arpeggio; a bright pluck as each transcript line arrives
  26-34 s  Step 3: sixteenth arpeggio, open hats
  34-44 s  compare: a breakdown without drums, warm pad and plucks, accents on each "Kept" and the merge
  44-48 s  services: an impact and the full groove again
  48-54 s  build: snare roll, filter up, noise riser
  54-60 s  end card: final C chord, a motif on the URL, tail fading to silence at 60 s
The times are the constants below, so the track can follow another cut of the video.
"""
import sys

import numpy as np
import soundfile as sf

SR = 48000
BPM = 120
BEAT = 60 / BPM
BAR = 4 * BEAT
LENGTH = 60.0
# cue points, in seconds: the video's scenes (all on bar lines) and its accents
SCENES = {"hook": (0, 6), "logo": (6, 10), "step1": (10, 18), "step2": (18, 26), "step3": (26, 34),
          "compare": (34, 44), "services": (44, 48), "build": (48, 54)}  # then the end card, 54-60
DROP, BUILD, END = 6.0, 48.0, 54.0
LINE_HITS = (19.5, 20.5, 21.5, 22.5)  # transcript lines arriving in Step 2
KEPT_HITS = (39.0, 39.5, 40.0)   # "Kept" in the compare scene
MERGE_HITS = (41.0, 42.0)        # the columns merging, "Saved as a new version"
N = int(LENGTH * SR)
rng = np.random.default_rng(7)


def midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def t_axis(n):
    return np.arange(n) / SR


def adsr(n, a=0.01, d=0.1, s=0.7, r=0.2, hold=None):
    """An envelope of n samples: attack, decay to sustain, release over the last r seconds."""
    env = np.ones(n) * s
    a_n, d_n, r_n = int(a * SR), int(d * SR), int(r * SR)
    if a_n:
        env[:a_n] = np.linspace(0, 1, a_n, endpoint=False)
    if d_n:
        seg = slice(a_n, min(n, a_n + d_n))
        k = np.arange(seg.stop - seg.start) / max(1, d_n)
        env[seg] = 1 - (1 - s) * k
    if r_n and r_n < n:
        env[-r_n:] *= np.linspace(1, 0, r_n) ** 2
    return env


def additive(freq, n, cutoff, harmonics=40, detune_cents=(0,), brightness=1.0, phase_seed=0):
    """A band-limited saw-like tone; cutoff (Hz, scalar or array per sample) shapes each harmonic like a
    soft low-pass, so a filter sweep costs nothing extra."""
    t = t_axis(n)
    out = np.zeros(n)
    cutoff = np.broadcast_to(np.asarray(cutoff, dtype=float), (n,))
    r = np.random.default_rng(phase_seed)
    for cents in detune_cents:
        f0 = freq * 2 ** (cents / 1200)
        for k in range(1, harmonics + 1):
            fk = f0 * k
            if fk > SR * 0.45:
                break
            gain = (1.0 / k ** brightness) / np.sqrt(1 + (fk / cutoff) ** 4)
            out += gain * np.sin(2 * np.pi * fk * t + r.uniform(0, 2 * np.pi))
    return out / len(detune_cents)


def place(track, sig, start):
    i = int(round(start * SR))
    if i >= len(track):
        return
    j = min(len(track), i + len(sig))
    track[i:j] += sig[: j - i]


def fft_filter(x, lo=None, hi=None):
    """Static band filter in the frequency domain (smooth edges)."""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = np.ones_like(f)
    if lo:
        g *= 1 / np.sqrt(1 + (lo / np.maximum(f, 1e-3)) ** 4)
    if hi:
        g *= 1 / np.sqrt(1 + (f / hi) ** 4)
    return np.fft.irfft(X * g, len(x))


def reverb(x, seconds=2.2, mix=0.25, seed=3):
    """Convolution with a synthetic, exponentially decaying, slightly darkened noise tail."""
    n = int(seconds * SR)
    r = np.random.default_rng(seed)
    ir = r.standard_normal(n) * np.exp(-6.9 * t_axis(n) / seconds)
    ir = fft_filter(ir, lo=200, hi=6000)
    ir /= np.sqrt(np.sum(ir ** 2))
    m = len(x) + n
    wet = np.fft.irfft(np.fft.rfft(x, m) * np.fft.rfft(ir, m), m)[: len(x)]
    return (1 - mix) * x + mix * wet * 0.9


# --- harmony ---------------------------------------------------------------------------------------
# C, G/B, Am, F (voicings around middle C), bass roots
CHORDS = [[60, 64, 67, 72], [59, 62, 67, 71], [57, 60, 64, 69], [57, 60, 65, 69]]
ROOTS = [36, 35, 33, 29]


def chord_at(bar):
    return CHORDS[bar % 4], ROOTS[bar % 4]


# --- instruments -----------------------------------------------------------------------------------
def pad(start, dur, notes, cutoff):
    n = int(dur * SR)
    sig = np.zeros(n)
    for i, m in enumerate(notes):
        sig += additive(midi(m), n, cutoff, harmonics=24, detune_cents=(-9, 0, 8), phase_seed=m * 7 + i)
    # thinned below 300 Hz, where the bass lives, so the low mids don't get muddy
    sig = fft_filter(sig, lo=300)
    return start, sig * adsr(n, a=0.35, d=0.4, s=0.85, r=0.5) * 0.10


def pluck(freq, dur=0.45, cutoff=3500, level=0.22):
    n = int(dur * SR)
    t = t_axis(n)
    cut = cutoff * np.exp(-t * 9) + 400
    sig = additive(freq, n, cut, harmonics=18, brightness=1.2, phase_seed=int(freq))
    return sig * np.exp(-t * 7.5) * adsr(n, a=0.002, d=0.02, s=1, r=0.05) * level


def bass(freq, dur):
    n = int(dur * SR)
    t = t_axis(n)
    sub = np.sin(2 * np.pi * freq * t)
    body = additive(freq, n, 700 * np.exp(-t * 5) + 250, harmonics=10, phase_seed=int(freq * 3))
    return (0.85 * sub + 0.18 * body) * adsr(n, a=0.004, d=0.12, s=0.7, r=0.06) * 0.30


def kick(level=0.9):
    n = int(0.42 * SR)
    t = t_axis(n)
    f = 45 + 105 * np.exp(-t * 32)
    phase = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(phase) * np.exp(-t * 7.5)
    click = fft_filter(rng.standard_normal(n), lo=2000, hi=9000) * np.exp(-t * 300) * 0.25
    return (body + click) * level


def clap(level=0.28):
    n = int(0.3 * SR)
    t = t_axis(n)
    noise = fft_filter(rng.standard_normal(n), lo=900, hi=7000)
    env = np.exp(-t * 18)
    for d in (0.011, 0.022):  # the few quick hits of a hand clap
        k = int(d * SR)
        env[:k] += 0.6 * np.exp(-t[:k] * 200)
    return noise * env * level


def hat(level=0.09, open_=False):
    n = int((0.22 if open_ else 0.06) * SR)
    t = t_axis(n)
    noise = fft_filter(rng.standard_normal(n), lo=7000, hi=16000)
    return noise * np.exp(-t * (14 if open_ else 70)) * level


def riser(dur, level=0.18):
    n = int(dur * SR)
    t = t_axis(n)
    x = rng.standard_normal(n)
    # a sweep: split into chunks, each band-passed a little higher
    out = np.zeros(n)
    chunks = 24
    for c in range(chunks):
        a, b = c * n // chunks, (c + 1) * n // chunks
        centre = 600 * 2 ** (c / chunks * 4)
        out[a:b] = fft_filter(x[a:b], lo=centre * 0.6, hi=centre * 1.8)
    return out * (t / dur) ** 2 * level


def impact(level=0.8):
    n = int(2.5 * SR)
    t = t_axis(n)
    boom = np.sin(2 * np.pi * (38 + 40 * np.exp(-t * 6)) * t) * np.exp(-t * 2.2)
    air = fft_filter(rng.standard_normal(n), lo=300, hi=4000) * np.exp(-t * 3) * 0.25
    return (boom + air) * level


# --- arrangement: one section per scene of the video -------------------------------------------------
music = np.zeros(N)   # tonal parts, ducked by the kick
drums = np.zeros(N)
fx = np.zeros(N)
duck = np.ones(N)


def scene(t):
    """The name of the scene playing at second t."""
    for name, (a, b) in SCENES.items():
        if a <= t < b:
            return name
    return "end"


for bar in range(int(LENGTH / BAR)):
    t0 = bar * BAR
    notes, root = chord_at(bar)
    name = scene(t0)
    # pad: closed in the hook, open in the steps, soft and warm for the compare breakdown, wider in the build
    if name == "hook":
        cut = np.linspace(500, 1400, int(BAR * SR + 0.5 * SR))
    elif name == "compare":
        cut = 1200
    elif name == "build":
        cut = np.linspace(1800 + (t0 - BUILD) * 500, 2300 + (t0 - BUILD) * 500, int(BAR * SR + 0.5 * SR))
    else:
        cut = 1800
    if name != "end":
        s, sig = pad(t0, BAR + 0.5, notes, cut)
        place(music, sig * (0.8 if name == "hook" else 1.15 if name == "compare" else 1.0), s)
    if name in ("hook", "compare"):  # sparse plucks: the hook, and the breakdown under the compare text
        for k, m in enumerate([notes[1] + 12, notes[2] + 12, notes[3] + 12] if name == "compare" else [notes[1] + 12, notes[2] + 12]):
            place(music, pluck(midi(m), level=0.13), t0 + 0.5 + k * (0.5 if name == "compare" else 1.0))
    # bass: long notes under the logo and the compare breakdown, then root and octave bounce in the groove
    if name in ("logo", "compare"):
        place(music, bass(midi(root), BAR + 0.08) * 0.8, t0)
    elif name in ("step1", "step2", "step3", "services", "build"):
        for b in range(4):
            place(music, bass(midi(root), 0.42), t0 + b * BEAT)
            place(music, bass(midi(root + 12), 0.2) * 0.6, t0 + b * BEAT + BEAT / 2)
    # arpeggio: eighths in Step 2, sixteenths from Step 3, back for the services and the build
    if name in ("step2", "step3", "services", "build"):
        pattern = [0, 1, 2, 3, 2, 1, 2, 3]
        sixteenths = name != "step2"
        for s16 in range(0, 16, 1 if sixteenths else 2):
            m = notes[pattern[s16 % 8]] + 12 + (12 if s16 >= 12 else 0)
            lvl = 0.10 if s16 % 4 else 0.14
            place(music, pluck(midi(m), dur=0.25, cutoff=2500 + max(0, t0 - 18) * 60, level=lvl), t0 + s16 * BEAT / 4)

# accents: a bright pluck as each transcript line arrives, on each "Kept", and on the merge
for t in LINE_HITS:
    place(music, pluck(midi(84), dur=0.4, cutoff=4000, level=0.12), t)
for t in KEPT_HITS:
    place(music, pluck(midi(79), dur=0.5, cutoff=3500, level=0.12), t)
for t in MERGE_HITS:
    place(fx, impact(0.22), t)
    place(music, pluck(midi(72), dur=0.8, cutoff=3000, level=0.13), t)

# drums
for i in range(int(DROP / BEAT), int(END / BEAT)):
    t = i * BEAT
    name = scene(t)
    if name in ("logo", "step1", "step2", "step3", "services", "build"):
        place(drums, kick(0.8 if name == "logo" else 0.85 if t < BUILD else 0.9), t)
        k = int(t * SR)
        m = min(N, k + int(0.28 * SR))
        duck[k:m] = np.minimum(duck[k:m], 0.45 + 0.55 * (np.arange(m - k) / (m - k)) ** 0.6)
    if name in ("step1", "step2", "step3", "services") and i % 2 == 1:
        place(drums, clap(), t)
    if name in ("step1", "step2", "step3", "services", "build"):
        place(drums, hat(0.08), t + BEAT / 2)
    if name in ("step3", "services"):
        place(drums, hat(0.04), t + BEAT / 4)
        place(drums, hat(0.04), t + 3 * BEAT / 4)
    if name == "step3" and i % 4 == 3:
        place(drums, hat(0.08, open_=True), t + BEAT / 2)

# the build: a snare roll getting faster and louder
t = BUILD
while t < END:
    frac = (t - BUILD) / (END - BUILD)
    step = BEAT / (2 if frac < 0.34 else 4 if frac < 0.67 else 8)
    place(drums, clap(0.10 + 0.22 * frac), t)
    t += step

# transitions: into the logo, out of the compare breakdown, into the end card
place(fx, riser(DROP - 0.5, 0.14), 0.5)
place(fx, impact(0.75), DROP)
place(fx, riser(3.5, 0.13), SCENES["services"][0] - 3.5)
place(fx, impact(0.5), SCENES["services"][0])
place(fx, riser(END - BUILD - 0.5, 0.18), BUILD + 0.5)
place(fx, impact(0.85), END)
_, final = pad(END, LENGTH - END, [48, 55, 60, 64, 67, 72], np.linspace(2200, 700, int((LENGTH - END) * SR)))
place(music, final * 1.4, END)
tail = bass(midi(36), LENGTH - END - 0.8) * 1.2
tail *= np.linspace(1, 0, len(tail)) ** 2  # a slow fade, not a cut
place(music, tail, END)
for k, m in enumerate([72, 76, 79, 84]):  # the motif, on the URL pill
    place(music, pluck(midi(m), dur=1.2, cutoff=3000, level=0.16), END + 1.0 + k * BEAT * 0.5)

mix = music * duck + drums * 0.9 + fx
mix = reverb(mix, seconds=2.4, mix=0.22)

# stereo: a small Haas widening of the tonal and fx parts, drums in the centre
wide = reverb(music * duck + fx, seconds=1.6, mix=0.5, seed=11)
d = int(0.012 * SR)
left = mix + 0.18 * wide
right = mix + 0.18 * np.concatenate([np.zeros(d), wide[:-d]])
stereo = np.stack([left, right], axis=1)

# master: gentle saturation, fade-in of 30 ms, fade-out over the last 1.5 s, peak at -1 dBFS
stereo = np.tanh(stereo * 1.3) / np.tanh(1.3)
stereo[: int(0.03 * SR)] *= np.linspace(0, 1, int(0.03 * SR))[:, None]
fade = int(1.5 * SR)
stereo[-fade:] *= (np.linspace(1, 0, fade) ** 1.5)[:, None]
stereo *= 10 ** (-1 / 20) / np.max(np.abs(stereo))

out = sys.argv[1] if len(sys.argv) > 1 else "sedjem-theme.wav"
sf.write(out, stereo.astype(np.float32), SR, subtype="PCM_24")
print("wrote", out, f"{len(stereo) / SR:.2f} s")
