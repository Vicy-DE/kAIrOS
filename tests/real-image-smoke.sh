#!/usr/bin/env bash
# Real image generation via sd-cli with the 8gb-tier manifest args for the DEFAULT model —
# Muse by Stable Yogi v3.5 Extended Q4_0 [R76 → Muse, docs/IMAGE-MODEL-MUSE-2026-10-10.md]
# (Qwen3-VL-4B LLM encoder + Qwen-Image VAE, euler/simple, 8 steps, cfg 1.0, 1024², no LoRA).
# Mirrors kernel _sd_model_args() + sd_job(): --diffusion-model/--vae/--llm + the section's
# gen_args, every value read from the manifest so this smoke never goes stale again.
# Manual GPU smoke on the stick (or any host with /opt/vibeos laid out like it); set MANIFEST
# to another tier file to smoke that one.
set -u
M=${MODELS:-/opt/vibeos/models}
MANIFEST=${MANIFEST:-$(cd "$(dirname "$0")/.." && pwd)/manifests/tier-8gb.json}
OUT=/tmp/kairos-real.png
rm -f "$OUT"
# the section's files + gen_args as the kernel reads them (one line each, the args NUL-free and space-safe)
mapfile -t SPEC < <(python3 - "$MANIFEST" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))["image"]
print(s["diffusion"]["file"]); print(s["vae"]["file"]); print(s["text_encoder"]["file"])
print("\t".join(s["gen_args"]))
PY
)
DIT=${SPEC[0]}; VAE=${SPEC[1]}; LLM=${SPEC[2]}
IFS=$'\t' read -r -a GEN <<< "${SPEC[3]}"
echo "generating ($(basename "$DIT"), ${GEN[*]})…"
T0=$(date +%s)
/opt/vibeos/bin/sd-cli \
    --diffusion-model "$M/$DIT" \
    --vae "$M/$VAE" \
    --llm "$M/$LLM" \
    "${GEN[@]}" -s 42 \
    -p "a glass koi fish, neon violet, dark background, high quality, sharp detail" \
    -o "$OUT" -v 2>/tmp/sd-real.log
echo "--- result (took $(($(date +%s)-T0))s) ---"
if [ -f "$OUT" ]; then
    python3 -c "import struct; d=open('$OUT','rb').read(24); w,h=struct.unpack('>II',d[16:24]); print(f'PNG OK: {w}x{h}, {len(open(\"$OUT\",\"rb\").read())} bytes')"
else
    echo "NO IMAGE — tail of log:"; tail -20 /tmp/sd-real.log
fi
grep -iE "CUDA|error|completed|sampling|wall|lora" /tmp/sd-real.log | tail -10
