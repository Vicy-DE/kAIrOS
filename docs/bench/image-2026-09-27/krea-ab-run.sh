#!/bin/bash
# Krea 2 A/B on the RTX 4070: kAIrOS manifest config vs the official Krea 2 Turbo config,
# short vs expanded prompts, fixed seed. Files already on disk only; outputs -> /out.
set -u
B=/home/layer/kairos/vibeos-cuda/engines/stable-diffusion.cpp/build/bin; MD=/home/layer/kairos/models/models/image; O=/tmp/claude-1000/-home-layer-kairos/1e4b3e9b-7af5-4e11-9972-8af84ffba6ce/scratchpad/krea-ab
export LD_LIBRARY_PATH=/home/layer/kairos/vibeos-cuda/lib
SUFFIX=", high quality, sharp detail"   # kernel prompts.IMAGE_PROMPT_HINT
SEED=42

declare -A P
P[cube_short]="a red cube on top of a blue sphere, a small green cone to the left of them, on a white table"
P[cube_long]="A studio photograph of three simple geometric objects arranged on a clean white table against a plain light-grey wall. In the center, a glossy blue sphere about the size of a grapefruit sits on the table, and a matte red cube rests balanced directly on top of the sphere. To the left of the sphere, slightly apart, stands a small green cone with its tip pointing up. Soft diffused studio lighting from the upper left casts gentle shadows to the right of each object. Eye-level camera angle, sharp focus, minimalist composition, photorealistic."
P[sign_short]='a wooden sign in a pine forest that says "KAIROS"'
P[sign_long]='A photograph of a rustic wooden sign standing on two posts at the edge of a pine forest trail. The weathered plank has the word "KAIROS" carved in large, clear capital letters and painted white. Tall pine trees and soft morning mist fill the background, with sunlight filtering through the branches onto the mossy ground and ferns around the posts. Eye-level medium shot, natural light, shallow depth of field keeping the sign sharp, realistic textures of wood grain and bark.'
P[cat_short]="cat astronaut drinking coffee on the moon"
P[cat_long]="A whimsical but photorealistic scene of a fluffy orange tabby cat wearing a white astronaut suit with the helmet visor open, sitting on the grey dusty surface of the moon and holding a steaming ceramic coffee mug in both paws. Small craters and boot prints surround the cat, and the Earth hangs large and blue in the black starry sky behind it. Hard, direct sunlight from the left creates crisp shadows on the lunar dust. Wide-angle medium shot at the cat's eye level, cinematic composition, highly detailed fur and fabric textures."

# A = kAIrOS manifest today (tier-8gb image.gen_args), B = official Krea 2 Turbo settings
cfg_args() {
  case $1 in
    A) echo "--steps 8 --cfg-scale 1.0 --sampling-method er_sde -W 768 -H 768 --diffusion-fa --vae-tiling --scheduler simple --vae $MD/wan_2.1_vae.safetensors" ;;
    B) echo "--steps 8 --cfg-scale 1.0 --sampling-method euler -W 1024 -H 1024 --diffusion-fa --vae-tiling --scheduler simple --vae $MD/qwen_image_vae.safetensors" ;;
  esac
}

for cfg in A B; do
  for key in cube_short cube_long sign_short sign_long cat_short cat_long; do
    name="${cfg}_${key}"
    [ -f "$O/$name.png" ] && { echo "skip $name"; continue; }
    t0=$(date +%s.%N)
    # shellcheck disable=SC2046
    timeout 900 $B/sd-cli --diffusion-model $MD/Krea-2-Turbo-Q4_K_M.gguf $(cfg_args $cfg) \
      -s $SEED --llm $MD/Qwen3VL-4B-Instruct-Q4_K_M.gguf \
      -p "${P[$key]}$SUFFIX" -o "$O/$name.png" -v > "$O/$name.log" 2>&1
    rc=$?
    wall=$(awk "BEGIN{printf \"%.1f\", $(date +%s.%N) - $t0}")
    cond=$(grep -o 'get_learned_condition completed, taking [0-9.]*s' "$O/$name.log" | grep -o '[0-9.]*s$')
    samp=$(grep -o 'sampling completed, taking [0-9.]*s' "$O/$name.log" | grep -o '[0-9.]*s$')
    toks=$(grep -o 'to [0-9]* tokens' "$O/$name.log" | sed -n 2p)
    echo "$name rc=$rc wall=${wall}s cond=$cond sampling=$samp prompt=$toks" | tee -a "$O/summary.txt"
  done
done
echo DONE | tee -a "$O/summary.txt"
