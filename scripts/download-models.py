#!/usr/bin/env python3
"""Download all models for a tier manifest into the models dir using aria2c.

Usage: download-models.py <manifest.json> [models_dir]
Skips files that already exist with the expected size (±2%). Resumable.
"""
import json
import os
import subprocess
import sys

manifest_path = sys.argv[1]
models_dir = sys.argv[2] if len(sys.argv) > 2 else "/opt/vibeos/models"

with open(manifest_path) as f:
    manifest = json.load(f)


def collect(node, out):
    if not isinstance(node, dict):
        return
    if "url" in node and "file" in node:
        out.append(node)
    for v in node.values():
        collect(v, out)


entries = []
collect(manifest, entries)

total_gb = sum(e.get("size_gb", 0) for e in entries)
print(f"[vibeos] {len(entries)} files, ~{total_gb:.1f} GB total -> {models_dir}", flush=True)

failed = []
for e in entries:
    dest = os.path.join(models_dir, e["file"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    expected = e.get("size_gb", 0) * 1e9
    if os.path.exists(dest) and expected and abs(os.path.getsize(dest) - expected) / expected < 0.02:
        print(f"[skip] {e['file']} (already present)", flush=True)
        continue
    print(f"[get ] {e['file']} ({e.get('size_gb', '?')} GB)", flush=True)
    r = subprocess.run([
        "aria2c", "-x8", "-s8", "--continue=true", "--summary-interval=30",
        "--console-log-level=warn", "--file-allocation=none",
        "-d", os.path.dirname(dest), "-o", os.path.basename(dest), e["url"],
    ])
    if r.returncode != 0:
        print(f"[FAIL] {e['file']} (aria2c exit {r.returncode})", flush=True)
        failed.append(e["file"])

if failed:
    print(f"[vibeos] DONE WITH ERRORS — failed: {failed}", flush=True)
    sys.exit(1)
print("[vibeos] ALL DOWNLOADS COMPLETE", flush=True)
