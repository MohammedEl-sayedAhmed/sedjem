#!/bin/sh
# Run benchmark configurations one after another, loading one model at a time.
# Each argument is model:audio:language, where model is
#   whisper (the default fine-tune, or $WHISPER_MODEL), large-v3, cohere, audar, qwen3asr, r2t2
# and audio is wav16k | phone | g711. Example:
#   SET=perle bench/run_configs.sh whisper:wav16k:ar cohere:g711:ar
# Environment: SET (test set, default perle), PROMPT (style hint for every run; unset = as in
# transcribe.py, i.e. only large-v3 gets the Egyptian hint), MODE (pipeline|clip).
set -eu
cd "$(dirname "$0")/.."
PY=.venv/bin/python
PORT=8090
LOG="${TMPDIR:-/tmp}/stt-bench-server.log"
pid=""
loaded=""

stop_server() {
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  pid=""
  loaded=""
}
trap stop_server EXIT
trap 'exit 130' INT TERM

start_server() {  # start llama-server for $1 and wait until it answers, at most 5 minutes
  stop_server
  THREADS=8 bench/serve.sh "$1" "$PORT" > "$LOG" 2>&1 &
  pid=$!
  i=0
  until curl -s "http://127.0.0.1:$PORT/health" | grep -q ok; do
    kill -0 "$pid" 2>/dev/null || { echo "llama-server for $1 exited; see $LOG" >&2; exit 1; }
    i=$((i + 1))
    [ "$i" -le 150 ] || { echo "llama-server for $1 not ready after 300 s; see $LOG" >&2; exit 1; }
    sleep 2
  done
  loaded=$1
}

for cfg in "$@"; do
  model=${cfg%%:*}; rest=${cfg#*:}; audio=${rest%%:*}; lang=${rest#*:}
  case "$model" in
    whisper)  stop_server; engine="--engine whisper${WHISPER_MODEL:+ --whisper-model $WHISPER_MODEL}" ;;
    large-v3) stop_server; engine="--engine whisper --whisper-model large-v3" ;;
    cohere)   stop_server; engine="--engine cohere" ;;
    audar|qwen3asr|r2t2)
      [ "$loaded" = "$model" ] || start_server "$model"
      engine="--engine llama --port $PORT" ;;
    *) echo "unknown model '$model' (whisper, large-v3, cohere, audar, qwen3asr, r2t2)" >&2; exit 1 ;;
  esac
  # $engine is split into words on purpose.
  # shellcheck disable=SC2086
  if [ -n "${PROMPT:-}" ]; then
    $PY -u bench/run_bench.py "${SET:-perle}" "$audio" $engine --language "$lang" --mode "${MODE:-pipeline}" --prompt "$PROMPT"
  else
    $PY -u bench/run_bench.py "${SET:-perle}" "$audio" $engine --language "$lang" --mode "${MODE:-pipeline}"
  fi
done
echo "CONFIGS DONE"
