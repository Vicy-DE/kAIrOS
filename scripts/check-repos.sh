#!/usr/bin/env bash
# List candidate model files (name + size) in the HF repos we plan to pull from.
set -u
for repo in \
  "leejet/Z-Image-Turbo-GGUF" \
  "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF" \
  "city96/umt5-xxl-encoder-gguf" \
  "QuantStack/Wan2.2-TI2V-5B-GGUF" \
  "unsloth/Qwen3-4B-Instruct-2507-GGUF" \
  "unsloth/Qwen2.5-Coder-7B-Instruct-GGUF" \
  "city96/Wan2.1-T2V-1.3B-gguf" \
  "Qwen/Qwen2.5-Coder-32B-Instruct-GGUF"
do
  echo "== $repo"
  python3 "$(dirname "$0")/list_repo.py" "$repo"
done
