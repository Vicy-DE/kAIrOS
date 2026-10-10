#!/usr/bin/env python3
"""Krea 2 prompt expansion, the official way (krea-ai/krea-2 docs/expansion.txt as the system
prompt, Comfy template sampling: temp 0.7, top_k 64, top_p 0.95, min_p 0.05, rep 1.05, 512 tok)
through llama-server with the SAME Qwen3-VL-4B GGUF Krea 2 uses as its text encoder.
Usage: expand.py <in.json {key: short prompt}> <out.json>"""
import json, subprocess, sys, time, urllib.request, os, re

R = os.path.dirname(os.path.abspath(__file__))
BIN = os.environ.get("KAIROS_HOME", os.path.expanduser("~/kairos")) + "/vibeos-cuda/engines/llama.cpp/build/bin/llama-server"
MODEL = os.environ.get("KAIROS_HOME", os.path.expanduser("~/kairos")) + "/models/models/image-candidates/Qwen3VL-4B-Instruct-Q8_0.gguf"
PORT = 8093
SYSTEM = open(f"{R}/krea-expansion.txt").read()


def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def clean(text):
    """one paragraph for the image model: drop any think block / planning, keep the last
    paragraph, strip ( ) [ ] because sd.cpp reads them as A1111 attention weights"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]
    text = paras[-1] if paras else text
    return re.sub(r"[()\[\]]", "", text).strip()


def main(inp, outp):
    prompts = json.load(open(inp))
    env = dict(os.environ, LD_LIBRARY_PATH=os.environ.get("KAIROS_HOME", os.path.expanduser("~/kairos")) + "/vibeos-cuda/lib")
    log = open(f"{R}/logs/llama-server-expand.log", "w")
    srv = subprocess.Popen([BIN, "-m", MODEL, "-ngl", "99", "-c", "8192", "--jinja",
                            "--port", str(PORT), "--host", "127.0.0.1"], stdout=log, stderr=log, env=env)
    try:
        for _ in range(240):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(1)
        out = {}
        for key, short in prompts.items():
            t0 = time.time()
            r = post("/v1/chat/completions", {
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": short}],
                "temperature": 0.7, "top_k": 64, "top_p": 0.95, "min_p": 0.05,
                "repeat_penalty": 1.05, "max_tokens": 512, "seed": 42})
            raw = r["choices"][0]["message"]["content"]
            out[key] = {"short": short, "raw": raw, "expanded": clean(raw),
                        "seconds": round(time.time() - t0, 1),
                        "tokens": r.get("usage", {}).get("completion_tokens")}
            print(f"{key}: {out[key]['seconds']} s, {out[key]['tokens']} tok -> {out[key]['expanded'][:120]}…", flush=True)
        json.dump(out, open(outp, "w"), indent=1, ensure_ascii=False)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=30)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
