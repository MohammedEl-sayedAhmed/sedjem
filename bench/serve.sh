#!/bin/sh
# Start llama-server for one Qwen3-ASR-family model, reachable from this machine only.
# Usage: bench/serve.sh r2t2|qwen3asr|audar [port]
set -eu
cd "$(dirname "$0")/.."
case "${1:?r2t2, qwen3asr or audar}" in
  r2t2)     m=models/Confucius4-R2T2-Q8_0.gguf; p=models/mmproj-Confucius4-R2T2-Q8_0.gguf ;;
  qwen3asr) m=models/Qwen3-ASR-1.7B-Q8_0.gguf; p=models/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf ;;
  audar)    m=models/Audar-ASR-V1-Turbo/Audar-ASR-V1-Turbo-Q4_K_M.gguf
            p=models/Audar-ASR-V1-Turbo/mmproj-Audar-ASR-V1-Turbo.gguf ;;
  *) echo "unknown model: $1" >&2; exit 1 ;;
esac
exec tools/llama/llama-b11165/llama-server --host 127.0.0.1 --port "${2:-8081}" \
  -m "$m" --mmproj "$p" -c 4096 -np 1 -t "${THREADS:-10}" --no-webui
