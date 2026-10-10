#!/usr/bin/env bash
# Real image generation via sd-cli with the 8gb-tier manifest args for the DEFAULT
# model — Krea 2 Turbo [R34] (Qwen3-VL-4B LLM encoder + Wan 2.1 VAE, er_sde/simple,
# 8 steps, cfg 1.0). Mirrors kernel sd_job(): --diffusion-model/--vae/--llm + gen_args.
# Z-Image (image_classic) is an add-on since R47 and is no longer what ships.
set -u
M=/opt/vibeos/models
OUT=/tmp/kairos-real.png
rm -f "$OUT"
echo "generating (Krea-2-Turbo Q4_K_M, er_sde/simple, 8 steps)…"
T0=$(date +%s)
/opt/vibeos/bin/sd-cli \
    --diffusion-model "$M/image/Krea-2-Turbo-Q4_K_M.gguf" \
    --vae "$M/image/wan_2.1_vae.safetensors" \
    --llm "$M/image/Qwen3VL-4B-Instruct-Q4_K_M.gguf" \
    --steps 8 --cfg-scale 1.0 --sampling-method er_sde --scheduler simple -W 768 -H 768 \
    --diffusion-fa --offload-to-cpu --vae-tiling -s 42 \
    -p "a glass koi fish, neon violet, dark background, high quality, sharp detail" \
    -o "$OUT" -v 2>/tmp/sd-real.log
echo "--- result (took $(($(date +%s)-T0))s) ---"
if [ -f "$OUT" ]; then
    python3 -c "import struct; d=open('$OUT','rb').read(24); w,h=struct.unpack('>II',d[16:24]); print(f'PNG OK: {w}x{h}, {len(open(\"$OUT\",\"rb\").read())} bytes')"
else
    echo "NO IMAGE — tail of log:"; tail -20 /tmp/sd-real.log
fi
grep -iE "CUDA|error|completed|sampling|wall" /tmp/sd-real.log | tail -10
