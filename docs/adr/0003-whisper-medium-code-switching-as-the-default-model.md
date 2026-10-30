---
status: Accepted
date: 2026-09-25
deciders: MohammedEl-sayedAhmed
---

# 3. The whisper-medium code-switching fine-tune as the default model

## Context

The project began by asking whether R2T2 could transcribe Egyptian Arabic–English calls, and went on to look for
the best free option that runs on a laptop without an NVIDIA graphics card
([use case](../01-use-case.md#the-original-question)). R2T2, its base model Qwen3-ASR, Audar-ASR, stock Whisper
large-v3, a Whisper medium fine-tune for Arabic–English code-switching and Cohere Transcribe Arabic were measured on
the public Perle set, phone audio, ArzEn and a test call, and later on real meetings
([trials](../02-trials-and-issues.md), [results](../03-results.md)).

What separates the models is whether English terms stay in Latin letters. The Qwen3-ASR family heard the English
words but wrote most of them in Arabic letters.

## Decision

- The default engine is the whisper-medium Arabic–English code-switching fine-tune, run by faster-whisper with
  int8 on the processor. On the test call it gave the most complete transcript and kept English terms best, so it
  became the default ([trial 25](../02-trials-and-issues.md)). It keeps 90% of English terms on Perle and 57% on
  the meetings, gives word timestamps, and is MIT-licensed.
- Cohere Transcribe Arabic, a Q4 GGUF run by transcribe.cpp, is the second local model, for Arabic-heavy meetings
  and for speed.
- Stock Whisper large-v3 gets a one-sentence Egyptian style hint, which took it from 39.7% to 22.4% on Perle with
  the whole clip.
- Every local model gets the same pipeline: Silero VAD cuts the recording into chunks of at most 25 seconds, each
  with 0.5 seconds of silence added at its end ([what happens inside](../05-command-line.md#what-happens-inside)).
- R2T2, Qwen3-ASR and Audar-ASR are not used ([also tried](../07-recommendation.md#also-tried)).

## Consequences

- English terms come out in English more often than with any other local model measured.
- The default is not the most accurate model overall: Cohere has the lower error rate on Perle (13.4% against
  18.5%) and on the meetings, makes fewer errors on Arabic words, and is about twice as fast (0.26 against 0.53
  times real time on the meetings). The docs say when to pick which ([recommendation](../07-recommendation.md)).
- The fine-tune's training data has a licence of its own to check before commercial use; its model card mentions
  the GPL.
- Graphics cards came later as an option, through CUDA for Whisper and Vulkan for Cohere
  ([GPU](../05-command-line.md#gpu)); the processor stays the baseline.
