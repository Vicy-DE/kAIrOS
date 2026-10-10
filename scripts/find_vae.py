#!/usr/bin/env python3
"""Probe candidate ungated HF repos for a FLUX/Z-Image VAE (ae.safetensors-like)."""
import json
import urllib.request

candidates = [
    "Comfy-Org/z_image_turbo",
    "Comfy-Org/flux1-schnell",
    "Comfy-Org/Lumina_Image_2.0_Repackaged",
    "second-state/FLUX.1-schnell-GGUF",
    "city96/FLUX.1-schnell-gguf",
    "StableDiffusionVN/Flux",
    "drbaph/FLUX.1-schnell_FP8",
]
for repo in candidates:
    url = f"https://huggingface.co/api/models/{repo}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)
        gated = data.get("gated", False)
        files = [s["rfilename"] for s in data.get("siblings", [])]
        vae = [f for f in files if "ae" in f.lower() and f.endswith(".safetensors")
               or "vae" in f.lower() and f.endswith(".safetensors")]
        print(f"== {repo}  gated={gated}")
        for f in vae:
            print(f"     VAE: {f}")
        if not vae:
            print(f"     (no obvious vae; {len(files)} files)")
    except Exception as e:
        print(f"== {repo}  ERROR {e}")
