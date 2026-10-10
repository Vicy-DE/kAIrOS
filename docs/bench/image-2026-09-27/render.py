#!/usr/bin/env python3
"""Render a job list with sd-cli (sd.cpp 7f410a3 CUDA build) on the host RTX 4070.
jobs.json: [{"name", "model", "prompt", "negative"?, "seed"?}] — model = a key of MODELS.
Skips jobs whose PNG exists; refuses to start a job while a foreign process holds the GPU;
logs wall time, sd.cpp phase times and peak VRAM to results.jsonl."""
import json, os, re, subprocess, sys, threading, time

R = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.environ.get("ENGINE_DIR", "/home/layer/kairos/vibeos-cuda")   # vibeos-cuda-next = sd.cpp master-929
SD = f"{ENGINE}/engines/stable-diffusion.cpp/build/bin/sd-cli"
ENV = dict(os.environ, LD_LIBRARY_PATH=f"{ENGINE}/lib")
M = "/home/layer/kairos/models/models/image"
C = "/home/layer/kairos/models/models/image-candidates"

COMMON = ["--diffusion-fa", "--vae-tiling"]
MODELS = {
    # kAIrOS 8 GB manifest today (tier-8gb image.gen_args)
    "krea_old": ["--diffusion-model", f"{M}/Krea-2-Turbo-Q4_K_M.gguf", "--llm", f"{M}/Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                 "--vae", f"{M}/wan_2.1_vae.safetensors", "--steps", "8", "--cfg-scale", "1.0",
                 "--sampling-method", "er_sde", "--scheduler", "simple", "-W", "768", "-H", "768", *COMMON],
    # the new workflow: official Krea 2 settings + Q8_0 encoder + Qwen-Image VAE, 1024²
    "krea_new": ["--diffusion-model", f"{M}/Krea-2-Turbo-Q4_K_M.gguf", "--llm", f"{C}/Qwen3VL-4B-Instruct-Q8_0.gguf",
                 "--vae", f"{M}/qwen_image_vae.safetensors", "--steps", "8", "--cfg-scale", "1.0",
                 "--sampling-method", "euler", "--scheduler", "simple", "-W", "1024", "-H", "1024", *COMMON],
    "zimage": ["--diffusion-model", f"{M}/z_image_turbo-Q4_0.gguf", "--llm", f"{M}/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
               "--vae", f"{M}/ae.safetensors", "--steps", "8", "--cfg-scale", "1.0",
               "--sampling-method", "euler", "-W", "1024", "-H", "1024", *COMMON],
    # sd.cpp docs/chroma.md flags (+ the manifest's 26 steps / CFG 4 / clip-on-cpu)
    "chroma": ["--diffusion-model", f"{M}/Chroma1-HD-Q4_K_S.gguf", "--t5xxl", f"{M}/t5-v1_1-xxl-encoder-Q4_K_M.gguf",
               "--vae", f"{M}/ae.safetensors", "--steps", "26", "--cfg-scale", "4.0", "--sampling-method", "euler",
               "-W", "1024", "-H", "1024", "--clip-on-cpu", "--model-args", "chroma_use_dit_mask=false", *COMMON],
    # Kroma = lodestones' Krea 2 fine-tune (Krea2 arch, shift 1.15 default, 8-12 steps, CFG 1)
    "kroma": ["--diffusion-model", f"{C}/kroma-v0.2-turbo.q4_0.gguf", "--llm", f"{C}/Qwen3VL-4B-Instruct-Q8_0.gguf",
              "--vae", f"{M}/qwen_image_vae.safetensors", "--steps", "10", "--cfg-scale", "1.0",
              "--sampling-method", "euler", "--scheduler", "simple", "-W", "1024", "-H", "1024", *COMMON],
    # Qwen-Image-2512 new workflow: Q4_K_M + Q8_0 encoder + Lightning 8-step LoRA, CFG 1
    "qwen2512": ["--diffusion-model", f"{C}/qwen-image-2512-Q4_K_M.gguf", "--llm", f"{C}/Qwen2.5-VL-7B-Instruct-Q8_0.gguf",
                 "--vae", f"{M}/qwen_image_vae.safetensors", "--lora-model-dir", C, "--steps", "8", "--cfg-scale", "1.0",
                 "--sampling-method", "euler", "--scheduler", "simple", "--flow-shift", "3",
                 "-W", "1024", "-H", "1024", *COMMON],
    # encoder A/B: krea_new with the shipped Q4_K_M encoder
    "krea_new_q4": ["--diffusion-model", f"{M}/Krea-2-Turbo-Q4_K_M.gguf", "--llm", f"{M}/Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                    "--vae", f"{M}/qwen_image_vae.safetensors", "--steps", "8", "--cfg-scale", "1.0",
                    "--sampling-method", "euler", "--scheduler", "simple", "-W", "1024", "-H", "1024", *COMMON],
    # the shipped Qwen-Image add-on (Q2_K, manifest args) with its Q4_K_S encoder vs the Q8_0 encoder
    "qwen_q2_encq4": ["--diffusion-model", f"{M}/Qwen_Image-Q2_K.gguf", "--llm", f"{M}/Qwen2.5-VL-7B-Instruct.Q4_K_S.gguf",
                      "--vae", f"{M}/qwen_image_vae.safetensors", "--steps", "40", "--cfg-scale", "2.5",
                      "--sampling-method", "euler", "--scheduler", "beta", "--flow-shift", "3", "-W", "1024", "-H", "1024", *COMMON],
    "qwen_q2_encq8": ["--diffusion-model", f"{M}/Qwen_Image-Q2_K.gguf", "--llm", f"{C}/Qwen2.5-VL-7B-Instruct-Q8_0.gguf",
                      "--vae", f"{M}/qwen_image_vae.safetensors", "--steps", "40", "--cfg-scale", "2.5",
                      "--sampling-method", "euler", "--scheduler", "beta", "--flow-shift", "3", "-W", "1024", "-H", "1024", *COMMON],
}
LORA = {"qwen2512": " <lora:Qwen-Image-2512-Lightning-8steps-V1.0-bf16:1>"}


def foreign_gpu_procs():
    out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    return out


def vram_sampler(stop, peak):
    while not stop.is_set():
        try:
            v = int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                   capture_output=True, text=True).stdout.split()[0])
            peak[0] = max(peak[0], v)
        except Exception:
            pass
        time.sleep(0.5)


def main(jobs_file):
    jobs = json.load(open(jobs_file))
    for j in jobs:
        out = f"{R}/out/{j['name']}.png"
        if os.path.exists(out):
            print(f"skip {j['name']}", flush=True)
            continue
        busy = foreign_gpu_procs()
        if busy:
            print(f"GPU BUSY (foreign process) — stopping before {j['name']}: {busy}", flush=True)
            sys.exit(3)
        args = [SD, *MODELS[j["model"]], "-s", str(j.get("seed", 42)),
                "-p", j["prompt"] + LORA.get(j["model"], ""), "-o", out, "-v"]
        if j.get("negative"):
            args += ["-n", j["negative"]]
        stop, peak = threading.Event(), [0]
        th = threading.Thread(target=vram_sampler, args=(stop, peak), daemon=True)
        th.start()
        t0 = time.time()
        with open(f"{R}/logs/{j['name']}.log", "w") as log:
            rc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, env=ENV).returncode
        wall = round(time.time() - t0, 1)
        stop.set(); th.join()
        txt = open(f"{R}/logs/{j['name']}.log", errors="ignore").read()
        g = lambda pat: (re.search(pat, txt).group(1) if re.search(pat, txt) else None)
        rec = {"name": j["name"], "model": j["model"], "rc": rc, "wall_s": wall, "peak_vram_mib": peak[0],
               "cond_s": g(r"get_learned_condition completed, taking ([0-9.]+)s"),
               "sampling_s": g(r"sampling completed, taking ([0-9.]+)s"),
               "decode_s": g(r"decode_first_stage completed, taking ([0-9.]+)s")}
        with open(f"{R}/results.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
