#!/usr/bin/env bash
# Smoke test: load the 7B model on the GPU and run one real inference.
set -u
MODEL=/opt/vibeos/models/llm/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf
LOG=/tmp/llama-smoke.log

/opt/vibeos/bin/llama-server -m "$MODEL" -ngl 99 -c 4096 \
    --host 127.0.0.1 --port 8088 > "$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT

for i in $(seq 1 90); do
    curl -fsS http://127.0.0.1:8088/health >/dev/null 2>&1 && break
    kill -0 $PID 2>/dev/null || { echo "SERVER DIED:"; tail -15 "$LOG"; exit 1; }
    sleep 2
done

echo "--- GPU offload lines ---"
grep -iE "CUDA[0-9]|offloaded|layers to GPU|using device|ggml_cuda" "$LOG" | head -8

echo "--- inference ---"
curl -s http://127.0.0.1:8088/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"messages":[{"role":"user","content":"Reply with exactly: READY"}],"max_tokens":16}' \
  | python3 -c 'import sys,json; print("REPLY:", json.load(sys.stdin)["choices"][0]["message"]["content"].strip())'

echo "--- timing ---"
grep -iE "prompt eval time|eval time|tokens per second" "$LOG" | tail -3
