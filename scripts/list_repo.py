#!/usr/bin/env python3
"""List model files (gguf/safetensors) with sizes for a HuggingFace repo."""
import json
import sys
import urllib.request

repo = sys.argv[1]
url = f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true"
try:
    with urllib.request.urlopen(url, timeout=30) as r:
        files = json.load(r)
except Exception as e:
    print(f"  (error: {e})")
    sys.exit(0)
if isinstance(files, dict):
    print(f"  (error: {files.get('error', '?')})")
    sys.exit(0)
for f in files:
    p = f["path"]
    if p.lower().endswith((".gguf", ".safetensors")):
        print(f"  {p}  {f.get('size', 0) / 1e9:.2f} GB")
