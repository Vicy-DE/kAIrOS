#!/usr/bin/env python3
"""Objective 'is it uncensored?' measure: Falconsai/nsfw_image_detection (ViT, Apache-2.0),
labels normal / nsfw, run on the CPU so it never competes with an sd-cli job for the GPU.
Usage (ComfyUI venv has torch + transformers): ~/ComfyUI/.venv/bin/python nsfw_score.py <png...>
Prints one JSON line per image and writes nsfw_scores.json next to this script."""
import json, os, sys
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForImageClassification
import torch

R = os.path.dirname(os.path.abspath(__file__))
CLS = f"{R}/classifier"
proc = AutoImageProcessor.from_pretrained(CLS)
model = AutoModelForImageClassification.from_pretrained(CLS).eval()
labels = model.config.id2label

path = f"{R}/nsfw_scores.json"
scores = json.load(open(path)) if os.path.exists(path) else {}
for f in sys.argv[1:]:
    with torch.no_grad():
        logits = model(**proc(images=Image.open(f).convert("RGB"), return_tensors="pt")).logits
    p = torch.softmax(logits, -1)[0]
    rec = {labels[i]: round(float(p[i]), 4) for i in range(len(p))}
    scores[os.path.basename(f)] = rec
    print(json.dumps({"image": os.path.basename(f), **rec}), flush=True)
json.dump(scores, open(path, "w"), indent=1, sort_keys=True)
