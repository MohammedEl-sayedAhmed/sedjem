"""Tell speakers apart by their voice.

1. Silero VAD finds the speech.
2. A 1.5 s window slides over the speech in 0.75 s steps (longer steps on very long
   recordings, so there are at most MAX_WINDOWS windows), and a speaker-embedding model
   (default NVIDIA TitaNet-small, trained with telephone speech) turns each window into a
   voiceprint that captures timbre and pitch, not words.
3. Spectral clustering groups the voiceprints by similarity into N speakers
   (N given, or estimated from the eigengap). The neighbour count is
   auto-tuned per recording (NME-SC, Park et al. 2019), so a few odd windows
   (noise, crosstalk) can't form a "speaker" of their own.
4. A 3-window majority filter removes single-window flips inside a speech segment.

Timeline.speaker_at(t) then gives who is talking at time t, and Timeline.changes(a, b)
the times where the speaker changes.

Instead of a voiceprint model, the model can be a NVIDIA Nemotron-3-Diarization folder
(nemotron.py), which does all of the above in one network and gives each speaker's probability
every 10 ms; activity_timeline() turns that into the same Timeline.
"""
from pathlib import Path

import numpy as np
import sherpa_onnx
from faster_whisper.vad import VadOptions, get_speech_timestamps

import nemotron

SR = 16000
WIN_S, HOP_S = 1.5, 0.75
MAX_WINDOWS = 2000  # longer recordings use a longer step to keep clustering fast
MAX_HOP_S = 5.0
# On six real meeting excerpts TitaNet-small credited 11.7% of words to the wrong speaker, against
# 25.7% for WeSpeaker ResNet34, and estimated the speaker count right in 5 of 6 (bench/compare_voiceprints.py).
MODEL = Path(__file__).resolve().parent / "models" / "diarization" / "nemo_en_titanet_small.onnx"


class Timeline:
    def __init__(self, centers, labels, embeddings, segments, hop_s):
        self.centers = centers  # window centres in seconds, ascending
        self.labels = labels  # speaker number per window, 1-based by first appearance
        self.embeddings = embeddings  # the voiceprints; None from activity_timeline()
        self.segments = segments  # VAD speech segments as (start, end) in seconds
        self.hop_s = hop_s  # the window step actually used

    def speaker_at(self, t):
        """Speaker of the voiceprint window nearest to time t, or None without any windows."""
        if not len(self.centers):
            return None
        i = int(np.searchsorted(self.centers, t))
        if i == 0 or i == len(self.centers):
            return int(self.labels[min(i, len(self.centers) - 1)])
        return int(self.labels[i - 1 if t - self.centers[i - 1] < self.centers[i] - t else i])

    def speaker_over(self, a, b):
        """Speaker of the stretch from a to b (a word, or a piece cut at speaker changes): the one
        at its middle."""
        return self.speaker_at((a + b) / 2)

    def changes(self, a, b):
        """Times strictly between a and b (seconds) where the speaker changes: the midpoint
        between two consecutive windows with different speakers."""
        i, j = np.searchsorted(self.centers, [a, b])
        c, s = self.centers[max(0, i - 1):j + 1], self.labels[max(0, i - 1):j + 1]
        mids = [(c[k] + c[k + 1]) / 2 for k in range(len(c) - 1) if s[k] != s[k + 1]]
        return [float(m) for m in mids if a < m < b]

    def summary(self):
        """Approximate talk time per speaker and how alike the speakers' average voiceprints are."""
        speakers = sorted({int(s) for s in self.labels})
        talk = {s: float(np.sum(self.labels == s) * self.hop_s) for s in speakers}
        if self.embeddings is None:
            return talk, []
        cents = [self.embeddings[self.labels == s].mean(0) for s in speakers]
        cents = [c / np.linalg.norm(c) for c in cents]
        sims = [float(cents[i] @ cents[j]) for i in range(len(cents)) for j in range(i + 1, len(cents))]
        return talk, sims


def speech_segments(audio):
    vad = VadOptions(min_silence_duration_ms=300, speech_pad_ms=100)
    return [(s["start"], s["end"]) for s in get_speech_timestamps(audio, vad)]


def windows(segments, hop):
    """(start, end, segment index) sample ranges: windows of WIN_S every `hop` samples."""
    win = int(WIN_S * SR)
    for n, (a, b) in enumerate(segments):
        if b - a < win:
            if b - a >= SR // 2:
                yield a, b, n
            continue
        starts = list(range(a, b - win + 1, hop))
        if starts[-1] + win < b:
            starts.append(b - win)
        for x in starts:
            yield x, x + win, n


def kmeans(x, k, restarts=10, iters=100):
    rng = np.random.default_rng(0)
    best, best_labels = np.inf, np.zeros(len(x), dtype=int)
    for _ in range(restarts):
        centers = [x[rng.integers(len(x))]]
        for _ in range(1, k):  # k-means++ seeding
            d = np.min(((x[:, None] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
            centers.append(x[rng.choice(len(x), p=d / d.sum())] if d.sum() > 0 else x[rng.integers(len(x))])
        centers = np.array(centers)
        labels = best_labels
        for _ in range(iters):
            labels = np.argmin(((x[:, None] - centers[None]) ** 2).sum(-1), axis=1)
            new = np.array([x[labels == j].mean(0) if np.any(labels == j) else centers[j] for j in range(k)])
            if np.allclose(new, centers):
                break
            centers = new
        inertia = ((x - centers[labels]) ** 2).sum()
        if inertia < best:
            best, best_labels = inertia, labels
    return best_labels


def spectral_cluster(emb, n_speakers=None, max_speakers=8):
    """NME-SC: binarized p-nearest-neighbour affinity, p chosen to maximize the normalized eigengap."""
    n = len(emb)
    if n == 0:
        return np.zeros(0, dtype=int)
    x = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    if n < 4 or (n_speakers and n <= n_speakers + 1):  # too few windows for a graph: cluster directly
        return kmeans(x, min(n_speakers, n)) if n_speakers and n > 1 else np.zeros(n, dtype=int)
    sim = x @ x.T
    order = np.argsort(-sim, axis=1)
    max_k = min(max(max_speakers, n_speakers or 0), n - 1)
    best_score, best_k, best_vecs = np.inf, 1, None
    # Each candidate p costs a full n x n eigendecomposition, so try fewer on long recordings.
    for p in np.unique(np.linspace(2, max(3, n // 4), 20 if n <= 1000 else 8).astype(int)):
        a = np.zeros_like(sim)
        np.put_along_axis(a, order[:, :p], 1.0, axis=1)
        a = (a + a.T) / 2
        vals, vecs = np.linalg.eigh(np.diag(a.sum(1)) - a)
        gaps = np.diff(vals[: max_k + 1])
        score = p / (gaps.max() / (vals[-1] + 1e-10) + 1e-10)
        if score < best_score:
            best_score, best_k, best_vecs = score, n_speakers or int(np.argmax(gaps)) + 1, vecs
    return kmeans(best_vecs[:, :best_k], best_k)


def cluster_capped(emb, n_speakers=None):
    """spectral_cluster on at most MAX_WINDOWS voiceprints. With more (many short speech segments,
    each needing its own window), cluster an evenly spaced subset and give every voiceprint the
    speaker whose average voiceprint it is closest to."""
    if len(emb) <= MAX_WINDOWS:
        return spectral_cluster(emb, n_speakers)
    x = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    idx = np.linspace(0, len(emb) - 1, MAX_WINDOWS).astype(int)
    sub = spectral_cluster(emb[idx], n_speakers)
    cents = np.array([x[idx][sub == k].mean(0) for k in sorted(set(sub.tolist()))])
    return np.argmax(x @ cents.T, axis=1)


def diarize(audio, n_speakers=None, threads=4, model=MODEL):
    """Build a Timeline for 16 kHz float32 audio; n_speakers=None estimates the count.
    `model` is any sherpa-onnx speaker-embedding ONNX file (WeSpeaker, TitaNet, CAM++, ...), or a
    Nemotron-3-Diarization folder, which hears at most 8 speakers."""
    if nemotron.is_model(model):
        return activity_timeline(nemotron.Model(model).probabilities(audio, threads), n_speakers)
    return timeline(*voiceprints(audio, threads, model), n_speakers)


def activity_timeline(probs, n_speakers=None, frame_s=0.01, step=8):
    """A Timeline from each speaker's probability every frame_s (Nemotron-3-Diarization). In steps of
    `step` frames, speech is where someone's average probability is above 0.5, and it goes to the most
    likely of the speakers heard at all, or of the n_speakers heard longest. A 3-step majority filter
    then removes single-step flips, as for the voiceprints."""
    talk = (probs > 0.5).sum(0)
    heard = [int(s) for s in np.argsort(-talk, kind="stable") if talk[s]]
    keep = np.array(heard[:n_speakers] if n_speakers else heard, dtype=int)
    starts = np.arange(0, len(probs), step)
    empty = Timeline(np.zeros(0), np.zeros(0, dtype=int), None, [], step * frame_s)
    if not len(keep) or not len(starts):
        return empty
    counts = np.diff(np.append(starts, len(probs)))
    mean = np.add.reduceat(probs, starts, axis=0) / counts[:, None]
    speech = np.flatnonzero(mean.max(1) > 0.5)
    if not len(speech):
        return empty
    labels = keep[mean[speech][:, keep].argmax(1)]
    run = np.concatenate([[0], np.cumsum(np.diff(speech) > 1)])  # stretches of consecutive speech steps
    smooth = labels.copy()
    for i in range(1, len(labels) - 1):
        if run[i - 1] == run[i] == run[i + 1] and labels[i - 1] == labels[i + 1] != labels[i]:
            smooth[i] = labels[i - 1]
    first = {}
    for s in smooth:
        first.setdefault(int(s), len(first) + 1)
    new_run = np.flatnonzero(np.diff(run)) + 1
    segments = [(float(starts[speech[a]] * frame_s), float((starts[speech[b]] + counts[speech[b]]) * frame_s))
                for a, b in zip(np.concatenate([[0], new_run]), np.concatenate([new_run - 1, [len(speech) - 1]]))]
    centers = (starts[speech] + counts[speech] / 2) * frame_s
    return ActivityTimeline(centers, np.array([first[int(s)] for s in smooth], dtype=int), None, segments,
                            step * frame_s, probs, keep, first, frame_s)


class ActivityTimeline(Timeline):
    """A Timeline that also keeps the probabilities it was made from, so a word goes to the speaker most
    likely over the whole word rather than at its middle: 10.0% of words to the wrong person on the
    meeting excerpts, against 10.2% (bench/compare_nemotron.py)."""

    def __init__(self, centers, labels, embeddings, segments, hop_s, probs, keep, numbers, frame_s):
        super().__init__(centers, labels, embeddings, segments, hop_s)
        self.probs, self.keep, self.frame_s = probs, keep, frame_s
        self.numbers = dict(numbers)  # the model's speaker index -> Speaker 1, 2, ...

    def speaker_over(self, a, b):
        i = min(max(int(a / self.frame_s), 0), len(self.probs) - 1)
        j = max(i + 1, min(int(np.ceil(b / self.frame_s)), len(self.probs)))
        s = int(self.keep[self.probs[i:j, self.keep].mean(0).argmax()])
        return self.numbers.setdefault(s, len(self.numbers) + 1)  # one too brief for the timeline: a new number


def voiceprints(audio, threads=4, model=MODEL):
    """(windows, voiceprints, speech segments, step): everything the clustering needs."""
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=threads))
    segments = speech_segments(audio)
    hop = HOP_S
    spans = list(windows(segments, int(hop * SR)))
    while len(spans) > MAX_WINDOWS and hop < MAX_HOP_S:  # a longer step thins out long segments only
        hop = min(MAX_HOP_S, hop * 1.05 * len(spans) / MAX_WINDOWS)
        spans = list(windows(segments, int(hop * SR)))
    embs = []
    for a, b, _ in spans:
        stream = extractor.create_stream()
        stream.accept_waveform(SR, audio[a:b])
        stream.input_finished()
        embs.append(extractor.compute(stream))
    return spans, np.array(embs).reshape(len(spans), extractor.dim), segments, hop


def timeline(spans, embs, segments, hop, n_speakers=None):
    """Cluster the voiceprints into speakers and smooth the labels into a Timeline."""
    labels = cluster_capped(embs, n_speakers)
    # Majority of each window and its two neighbours, within one speech segment.
    smooth = labels.copy()
    for i in range(1, len(labels) - 1):
        same_segment = spans[i - 1][2] == spans[i][2] == spans[i + 1][2]
        if same_segment and labels[i - 1] == labels[i + 1] != labels[i]:
            smooth[i] = labels[i - 1]
    first = {}
    for s in smooth:
        first.setdefault(int(s), len(first) + 1)
    centers = np.array([(a + b) / 2 / SR for a, b, _ in spans])
    return Timeline(centers, np.array([first[int(s)] for s in smooth], dtype=int), embs,
                    [(a / SR, b / SR) for a, b in segments], hop)
