"""Tell speakers apart with NVIDIA Nemotron-3-Diarization, in NumPy.

Nemotron-3-Diarization (about 100M parameters, OpenMDW 1.1 licence) gives, every 10 ms, the
probability that each of up to 8 speakers is talking, the speakers numbered by when they first speak.
One model does what speakers.py does in three steps (speech detection, voiceprints, clustering), and
it hears two people talking at once.

This runs NVIDIA's weights (model.safetensors, config.json and processor_config.json from
huggingface.co/nvidia/Nemotron-3-Diarization) with NumPy alone, following the Transformers
implementation in its offline mode (Nemotron3DiarizationForAudioFrameClassification):

1. A log-mel spectrogram, 128 bands every 10 ms, and 8 of its frames stacked into one 80 ms frame.
2. The recording in chunks of 340 frames (27.2 s) that also see the next 40. Each chunk goes through
   31 transformer layers together with up to 264 earlier frames kept as a speaker cache (for each
   speaker, the frames that most clearly belong to them) and the 40 frames just before it, so the
   speaker numbers stay the same from one chunk to the next.
3. The speaker probabilities, upsampled back to every 10 ms.

bench/check_nemotron.py compares it with Transformers on real recordings, and tests/test_nemotron.py
with a tiny random model. speakers.diarize() uses it when its model is this folder.
"""
import json
import math
from pathlib import Path

import numpy as np

LOG_GUARD = 2.0 ** -24  # added to the mel energies before the log, as in NeMo


def is_model(path):
    """Whether path is a Nemotron-3-Diarization folder (rather than a voiceprint ONNX file)."""
    return (Path(path) / "model.safetensors").is_file()


def load_safetensors(path):
    """{name: float32 array} from a .safetensors file, read without copying (memory-mapped)."""
    raw = np.memmap(path, dtype=np.uint8, mode="r")
    size = int(raw[:8].view("<u8")[0])
    header = json.loads(bytes(raw[8:8 + size]))
    header.pop("__metadata__", None)
    start = 8 + size
    tensors = {}
    for name, t in header.items():
        if t["dtype"] != "F32":
            raise ValueError(f"{path}: {name} is {t['dtype']}, only F32 is supported")
        a, b = t["data_offsets"]
        tensors[name] = raw[start + a:start + b].view("<f4").reshape(t["shape"])
    return tensors


def mel_filters(sr, n_fft, n_mels):
    """Triangular mel filters on Slaney's mel scale with Slaney's area normalization, as
    librosa.filters.mel(sr=sr, n_fft=n_fft, n_mels=n_mels, norm="slaney") makes them."""
    f_sp, min_log_hz, min_log_mel, logstep = 200.0 / 3, 1000.0, 15.0, math.log(6.4) / 27.0

    def hz_to_mel(f):
        return min_log_mel + math.log(f / min_log_hz) / logstep if f >= min_log_hz else f / f_sp

    mels = np.linspace(hz_to_mel(0.0), hz_to_mel(sr / 2), n_mels + 2)
    hz = np.where(mels >= min_log_mel, min_log_hz * np.exp(logstep * (mels - min_log_mel)), f_sp * mels)
    ramps = hz[:, None] - np.fft.rfftfreq(n_fft, 1.0 / sr)[None, :]
    width = np.diff(hz)
    weights = np.maximum(0, np.minimum(-ramps[:-2] / width[:-1, None], ramps[2:] / width[1:, None]))
    weights = weights.astype(np.float32)  # librosa stores the triangles as float32, then scales them
    return (weights * (2.0 / (hz[2:] - hz[:-2]))[:, None]).astype(np.float32)


def layer_norm(x, weight, bias, eps=1e-5):
    mean = x.mean(-1, keepdims=True)
    centred = x - mean
    var = (centred * centred).mean(-1, keepdims=True)
    return centred / np.sqrt(var + eps) * weight + bias


def gelu(x):
    """0.5 x (1 + erf(x / sqrt 2)), in place, with erf from Abramowitz and Stegun 7.1.26 (error below
    1.5e-7). In place because fresh arrays this size each cost the operating system a round of pages."""
    z = np.abs(x)
    z *= np.float32(1 / math.sqrt(2))
    t = z * np.float32(0.3275911)
    t += 1
    np.reciprocal(t, out=t)
    poly = t * np.float32(1.061405429)
    for c in (-1.453152027, 1.421413741, -0.284496736, 0.254829592):
        poly += np.float32(c)
        poly *= t
    z *= z
    np.negative(z, out=z)
    np.exp(z, out=z)
    poly *= z  # 1 - erf(|x| / sqrt 2)
    np.subtract(1, poly, out=poly)
    np.copysign(poly, x, out=poly)
    poly += 1
    x *= poly
    x *= np.float32(0.5)
    return x


def sigmoid(x):
    return 0.5 + 0.5 * np.tanh(0.5 * x)  # 1 / (1 + exp(-x)), without overflow


class Model:
    def __init__(self, folder):
        folder = Path(folder)
        cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        audio, head, stream = cfg["audio_config"], cfg["head_config"], cfg["streaming_config"]
        self.hidden, self.heads = audio["hidden_size"], audio["num_attention_heads"]
        self.head_dim = self.hidden // self.heads
        if audio.get("num_key_value_heads", self.heads) != self.heads:
            raise ValueError("grouped key/value heads are not supported")
        self.layers, self.factor = audio["num_hidden_layers"], audio["subsampling_factor"]
        rope = audio.get("rope_parameters") or {}
        if rope.get("rope_type", "default") != "default" or rope.get("partial_rotary_factor", 1.0) != 1.0:
            raise ValueError("only full rotary embeddings of the default type are supported")
        self.inv_freq = (1.0 / (np.float32(rope.get("rope_theta", 10000.0)) ** (
            np.arange(0, self.head_dim, 2, dtype=np.float32) / np.float32(self.head_dim)))).astype(np.float32)
        self.speakers = head["num_speakers"]
        # offline mode: the chunking and FIFO sizes of the top-level config, the speaker-cache policy of the
        # streaming one
        self.chunk, self.lookahead = cfg["chunk_length"], cfg["chunk_right_context"]
        self.fifo_length, self.update_period = cfg["fifo_length"], cfg["speaker_cache_update_period"]
        self.cache_length = stream["speaker_cache_length"]
        self.silence_frames = stream["speaker_cache_silence_frames_per_speaker"]
        self.score_threshold = stream["prediction_score_threshold"]
        self.latest_boost = stream["latest_frames_score_boost"]
        budget = self.cache_length // self.speakers - self.silence_frames  # cache frames per speaker
        self.min_positive = math.floor(budget * stream["min_positive_scores_rate"])
        self.strong_boosted = math.floor(budget * stream["strong_boost_rate"])
        self.weak_boosted = math.floor(budget * stream["weak_boost_rate"])

        fe = json.loads((folder / "processor_config.json").read_text(encoding="utf-8"))["feature_extractor"]
        self.sr, self.n_fft, self.hop = fe["sampling_rate"], fe["n_fft"], fe["hop_length"]
        self.preemphasis = fe.get("preemphasis") or 0.0
        win = fe["win_length"]
        hann = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(win) / (win - 1))).astype(np.float32)  # symmetric
        self.window = np.zeros(self.n_fft, np.float32)
        self.window[(self.n_fft - win) // 2:(self.n_fft - win) // 2 + win] = hann  # centred, as torch.stft
        self.filters = mel_filters(self.sr, self.n_fft, fe["feature_size"])
        if fe["feature_size"] != audio["num_mel_bins"]:
            raise ValueError("the feature extractor and the model disagree on the number of mel bands")

        self.w = load_safetensors(folder / "model.safetensors")

    def embeddings(self, audio):
        """The model's input every 80 ms, (frames, hidden), the number of 10 ms frames, and which 80 ms
        frames hold audio: the spectrogram's last frame, which reaches past the audio, is zeroed and,
        alone in its group, masked."""
        x = np.asarray(audio, dtype=np.float32)
        if self.preemphasis:
            x = np.concatenate([x[:1], x[1:] - np.float32(self.preemphasis) * x[:-1]])
        x = np.pad(x, self.n_fft // 2)  # torch.stft(center=True, pad_mode="constant")
        n = 1 + (len(x) - self.n_fft) // self.hop
        valid = len(audio) // self.hop
        frames = np.lib.stride_tricks.sliding_window_view(x, self.n_fft)[::self.hop]
        proj = self.w["model.audio_tower.embedder.projection.weight"]
        groups = -(-n // self.factor)
        out = np.empty((groups, self.hidden), np.float32)
        step = 512 * self.factor  # spectrogram frames per block
        for a in range(0, n, step):
            b = min(n, a + step)
            power = np.abs(np.fft.rfft(frames[a:b] * self.window, axis=1)) ** 2
            mel = np.log(power.astype(np.float32) @ self.filters.T + np.float32(LOG_GUARD))
            mel[max(0, valid - a):] = 0
            pad = -len(mel) % self.factor  # zeros for the last incomplete group
            mel = np.pad(mel, ((0, pad), (0, 0))).reshape(-1, self.factor * mel.shape[1])
            out[a // self.factor:a // self.factor + len(mel)] = mel @ proj.T
        mask = np.arange(groups) * self.factor < valid
        return out, n, mask

    def rope(self, length):
        freqs = np.arange(length, dtype=np.float32)[:, None] * self.inv_freq[None, :]
        emb = np.concatenate([freqs, freqs], axis=-1)
        return np.cos(emb), np.sin(emb)

    def network(self, x, key_mask=None):
        """Speaker logits every 10 ms, (len(x) * factor, speakers), for one step's 80 ms input frames:
        the cached ones, the chunk and its look-ahead."""
        w, length = self.w, len(x)
        h = layer_norm(x, w["model.audio_tower.input_layer_norm.weight"], w["model.audio_tower.input_layer_norm.bias"])
        cos, sin = self.rope(length)
        cos, sin = cos[:, None], sin[:, None]  # the same for every head
        half, d = self.head_dim // 2, self.head_dim
        additive = None if key_mask is None or key_mask.all() else np.where(key_mask, 0, -np.inf).astype(np.float32)
        scale = np.float32(d ** -0.5)  # 1/8: applied to the queries, which gives the same scores exactly

        def heads(t):  # (length, hidden) -> rotated, (heads, length, head_dim), contiguous for BLAS
            t = t.reshape(length, self.heads, d)
            t = t * cos + np.concatenate([-t[..., half:], t[..., :half]], axis=-1) * sin
            return np.ascontiguousarray(t.transpose(1, 0, 2))

        attended = np.empty((length, self.hidden), np.float32)
        scores = np.empty((length, length), np.float32)
        for i in range(self.layers):
            p = f"model.audio_tower.layers.{i}."
            y = layer_norm(h, w[p + "layer_norm1.weight"], w[p + "layer_norm1.bias"])
            q = heads(y @ w[p + "self_attn.q_proj.weight"].T)
            q *= scale
            k = heads(y @ w[p + "self_attn.k_proj.weight"].T)
            v = np.ascontiguousarray((y @ w[p + "self_attn.v_proj.weight"].T).reshape(length, self.heads, d)
                                     .transpose(1, 0, 2))
            for j in range(self.heads):  # one head at a time keeps the scores in the CPU's cache
                np.matmul(q[j], k[j].T, out=scores)
                if additive is not None:
                    scores += additive
                scores -= scores.max(-1, keepdims=True)
                np.exp(scores, out=scores)
                scores /= scores.sum(-1, keepdims=True)
                np.matmul(scores, v[j], out=attended[:, j * d:(j + 1) * d])
            h = h + attended @ w[p + "self_attn.o_proj.weight"].T + w[p + "self_attn.o_proj.bias"]
            y = layer_norm(h, w[p + "layer_norm2.weight"], w[p + "layer_norm2.bias"])
            fc1, b1 = w[p + "mlp.fc1.weight"].T, w[p + "mlp.fc1.bias"]
            fc2, b2 = w[p + "mlp.fc2.weight"].T, w[p + "mlp.fc2.bias"]
            for a in range(0, length, 128):  # in blocks of rows, so the wide middle layer stays in the cache
                h[a:a + 128] += gelu(y[a:a + 128] @ fc1 + b1) @ fc2 + b2
        h = layer_norm(h, w["model.audio_tower.layer_norm.weight"], w["model.audio_tower.layer_norm.bias"])
        h = h @ w["model.proj.weight"].T + w["model.proj.bias"]
        # sub-pixel upsampling: a 3-tap convolution to factor x the channels, read as factor frames each
        conv = w["model.upsampler.conv.weight"]
        padded = np.pad(h, ((1, 1), (0, 0)))
        up = (padded[:-2] @ conv[:, :, 0].T + padded[1:-1] @ conv[:, :, 1].T + padded[2:] @ conv[:, :, 2].T
              + w["model.upsampler.conv.bias"])
        up = up.reshape(length * self.factor, -1)
        up = np.maximum(up, 0) @ w["classifier.dense.weight"].T + w["classifier.dense.bias"]
        return np.maximum(up, 0) @ w["classifier.out_proj.weight"].T + w["classifier.out_proj.bias"]

    def probabilities(self, audio, threads=4):
        """(frames, speakers): the probability that each speaker talks, every 10 ms of the 16 kHz audio.
        NumPy's matrix library runs on `threads` threads meanwhile: by default it takes every core, which
        on a laptop processor with efficiency cores made it twice as slow as 4 threads."""
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=threads, user_api="blas"):
            return self._probabilities(audio)

    def _probabilities(self, audio):
        embeds, n, mask = self.embeddings(audio)
        cache = Cache(self)
        logits = []
        for start in range(0, len(embeds), self.chunk):
            end = min(start + self.chunk, len(embeds))
            chunk = embeds[start:min(end + self.lookahead, len(embeds))]
            cached = cache.frames()
            x = np.concatenate([cached, chunk])
            step_mask = None if mask.all() else np.concatenate(
                [np.ones(len(cached), bool), mask[start:start + len(chunk)]])
            out = self.network(x, step_mask)
            cache.update(x, out, end - start, step_mask)
            logits.append(out[len(cached) * self.factor:(len(cached) + end - start) * self.factor])
        return sigmoid(np.concatenate(logits)[:n]) if logits else np.zeros((0, self.speakers), np.float32)


class Cache:
    """The speaker cache and the FIFO queue of recent frames that every chunk attends to
    (Transformers' Nemotron3DiarizationSpeakerCache, for one recording)."""

    def __init__(self, model):
        self.m = model
        self.embeds = np.zeros((0, model.hidden), np.float32)
        self.probs = np.zeros((0, model.speakers), np.float32)
        self.fifo = np.zeros((0, model.hidden), np.float32)
        self.compressed = False

    def frames(self):
        return np.concatenate([self.embeds, self.fifo])

    def update(self, x, logits, chunk_frames, mask=None):
        """Push a processed chunk to the FIFO queue; when it overflows, move its oldest frames to the
        speaker cache, compressed to its best frames per speaker once full."""
        m = self.m
        probs = sigmoid(logits).reshape(-1, m.factor, m.speakers).mean(1)  # per 80 ms frame
        if mask is not None:
            probs = probs * mask[:, None]
        cached, queued = len(self.embeds), len(self.fifo)
        fifo = np.concatenate([self.fifo, x[cached + queued:cached + queued + chunk_frames]])
        popped = 0 if len(fifo) <= m.fifo_length else min(max(m.update_period, len(fifo) - m.fifo_length), len(fifo))
        if popped:
            fifo_probs = probs[cached:cached + len(fifo)]
            # an uncompressed cache still holds plain frames, which this step scored again; a compressed one
            # is out of order, so only the probabilities stored with it apply
            stored = self.probs if self.compressed else probs[:cached]
            embeds = np.concatenate([self.embeds, fifo[:popped]])
            kept = np.concatenate([stored, fifo_probs[:popped]])
            fifo = fifo[popped:]
            if len(embeds) > m.cache_length:
                embeds, kept = self.compress(embeds, kept)
                self.compressed = True
            self.embeds, self.probs = embeds, kept.astype(np.float32)
        self.fifo = fifo

    def scores(self, probs):
        """How clearly each frame belongs to each speaker; -inf for frames that don't."""
        m = self.m
        log_p = np.log(np.maximum(probs, m.score_threshold))
        log_q = np.log(np.maximum(1 - probs, m.score_threshold))
        scores = log_p - log_q + log_q.sum(-1, keepdims=True) - np.float32(math.log(0.5))
        speech = probs > 0.5
        scores[~speech] = -np.inf
        positive = scores > 0
        enough = positive.sum(0, keepdims=True) >= m.min_positive
        scores[~positive & speech & enough] = -np.inf
        return scores

    @staticmethod
    def boost(scores, count, amount):
        """Add amount to each speaker's count best frames."""
        top = np.argpartition(-scores, count - 1, axis=0)[:count]
        scores[top, np.arange(scores.shape[1])] += np.float32(amount)
        return scores

    def compress(self, embeds, probs):
        """Keep cache_length frames: the best ones per speaker, grouped by speaker in their own order,
        with silence_frames slots per speaker holding the learned silence embedding."""
        m = self.m
        frames, speakers = probs.shape
        scores = self.scores(probs)
        scores[m.cache_length:] += np.float32(m.latest_boost)  # the frames just popped from the queue
        scores = self.boost(scores, m.strong_boosted, -2.0 * math.log(0.5))
        scores = self.boost(scores, m.weak_boosted, -math.log(0.5))
        scores = np.concatenate([scores, np.full((m.silence_frames, speakers), np.inf, np.float32)])
        embeds = np.concatenate([embeds, m.w["silence_embeds"][None]])
        probs = np.concatenate([probs, np.zeros((1, speakers), probs.dtype)])
        scored = frames + m.silence_frames
        flat = scores.T.reshape(-1)  # speaker by speaker
        top = np.argpartition(flat, len(flat) - m.cache_length)[-m.cache_length:]
        top = np.where(flat[top] == -np.inf, scored * speakers, top)  # nothing worth keeping: silence
        top.sort()
        rows = np.where(top == scored * speakers, frames, np.minimum(top % scored, frames))
        return embeds[rows], probs[rows]
