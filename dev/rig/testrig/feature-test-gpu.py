#!/usr/bin/env python3
"""feature-test-gpu.py — runs INSIDE a kAIrOS guest (as root, loopback -> no token needed) and drives the
user-facing features end to end on the passed-through GPU (VM test 2026-09-29; successor of the July
feature-test.sh). Stdlib only. Every step appends to <out>/results.json + <out>/ft.log right away, so a
cut-off leaves the finished steps on disk.

    python3 feature-test-gpu.py [--out DIR] [step ...]      steps: status app image images genimg
                                                              uncensored video music game vision  (default: all)
Artifacts: app-<slug>.html, img-*.png, video.mp4, music.wav, genimg page + pictures, kernel journal excerpts.
"""
import concurrent.futures as cf, json, os, re, subprocess, sys, threading, time, urllib.parse, urllib.request, urllib.error

K = "http://127.0.0.1:1337"
args = sys.argv[1:]
OUT = "/root/vm0929/ft"
if args[:1] == ["--out"]:
    OUT, args = args[1], args[2:]
os.makedirs(OUT, exist_ok=True)
STEPS = args or ["status", "app", "image", "images", "genimg", "uncensored", "video", "music", "game", "vision"]
RES_PATH = f"{OUT}/results.json"
RESULTS = json.load(open(RES_PATH)) if os.path.exists(RES_PATH) else {"steps": []}
LOG = open(f"{OUT}/ft.log", "a")
APP_PROMPT = "a text editor with find and replace, word count, undo and save"
STATE = {}


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True); LOG.write(line + "\n"); LOG.flush()


def save():
    tmp = RES_PATH + ".tmp"
    json.dump(RESULTS, open(tmp, "w"), indent=1, ensure_ascii=False); os.replace(tmp, RES_PATH)


def sh(cmd, timeout=120):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def api(method, path, body=None, timeout=120, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(K + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            p = r.read()
            return (p if raw else (json.loads(p) if "json" in r.headers.get("Content-Type", "") else p.decode("utf-8", "replace"))), r.status
    except urllib.error.HTTPError as e:
        p = e.read()
        try:
            return json.loads(p), e.code
        except ValueError:
            return p.decode("utf-8", "replace"), e.code


class Vram(threading.Thread):
    """peak GPU memory (nvidia-smi every 2 s) during the current step"""
    def __init__(self):
        super().__init__(daemon=True); self.peak = 0; self.on = True
    def run(self):
        while self.on:
            r = sh("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits", timeout=20)
            try:
                self.peak = max(self.peak, int(r.stdout.strip().split("\n")[0]))
            except ValueError:
                pass
            time.sleep(2)


VRAM = Vram(); VRAM.start()


def journal_since(t0, grep=None, n=40):
    r = sh(f"journalctl -u kairos --no-pager -o cat --since @{int(t0)}", timeout=60)
    lines = [l for l in r.stdout.splitlines() if grep is None or re.search(grep, l)]
    return lines[-n:]


def poll_job(jid, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j, _ = api("GET", f"/api/job/{jid}")
        if isinstance(j, dict) and j.get("status") in ("done", "error"):
            return j
        time.sleep(2)
    raise RuntimeError(f"job {jid} timed out after {timeout} s")


def fetch(path, dest):
    data, code = api("GET", path, raw=True, timeout=300)
    if code != 200:
        raise RuntimeError(f"GET {path}: HTTP {code}")
    open(dest, "wb").write(data)
    return len(data)


def png_info(path):
    r = sh(f"python3 -c \"from PIL import Image; im=Image.open('{path}'); print(im.size[0], im.size[1], im.mode)\"")
    return r.stdout.strip()


def ffprobe(path):
    r = sh(f"ffprobe -v error -show_entries stream=codec_name,width,height,nb_frames,r_frame_rate,sample_rate,channels "
           f"-show_entries format=duration -of json {path}")
    try:
        return json.loads(r.stdout)
    except ValueError:
        return r.stdout[-400:] + r.stderr[-400:]


def step(name, fn):
    VRAM.peak = 0
    t0 = time.time(); log(f"== {name}")
    rec = {"step": name, "started": time.strftime("%H:%M:%S")}
    try:
        fn(rec, t0)
        rec.update(ok=True)
    except Exception as e:
        rec.update(ok=False, error=f"{type(e).__name__}: {str(e)[:900]}")
    rec.update(seconds=round(time.time() - t0, 1), peak_vram_mib=VRAM.peak)
    log(f"   {'ok' if rec['ok'] else 'FAILED'} in {rec['seconds']} s, peak VRAM {VRAM.peak} MiB"
        + ("" if rec["ok"] else f" — {rec['error'][:300]}"))
    RESULTS["steps"].append(rec); save()


# ---------------------------------------------------------------------------------------------- steps
def s_status(rec, t0):
    s, _ = api("GET", "/api/status")
    rec["status"] = {k: s.get(k) for k in ("tier", "engine", "engine_info", "llm", "llm_options", "image",
                                           "image_sessions", "video", "js_gate", "state", "loaded")}
    rec["status"]["engines"] = {k: (s.get("engines") or {}).get(k) for k in ("status", "installed", "problems")}
    rec["nvidia"] = sh("nvidia-smi --query-gpu=name,memory.used,memory.total,driver_version --format=csv,noheader").stdout.strip()
    m, _ = api("GET", "/api/models")
    rec["models"] = {"image": [{k: x.get(k) for k in ("key", "name", "present", "active", "expander")} for x in m.get("models", [])],
                     "extras": [{k: e.get(k) for k in ("key", "present", "available", "addon")} for e in m.get("extras", [])],
                     "sessions": m.get("sessions"), "expand": m.get("expand")}
    assert (s.get("engines") or {}).get("status") == "ok", s.get("engines")
    assert s.get("image_sessions") == "sd-server", s.get("image_sessions")
    assert str(s.get("llm", "")).startswith("Gemma 4"), s.get("llm")
    assert (s.get("js_gate") or {}).get("enabled"), s.get("js_gate")


def sse_app(body, timeout=1800):
    data = json.dumps(body).encode()
    req = urllib.request.Request(K + "/api/app", data=data, method="POST", headers={"Content-Type": "application/json"})
    events, deltas, buf = [], [], b""
    t_first = None; t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                ev = d = None
                for line in frame.decode("utf-8", "ignore").splitlines():
                    if line.startswith("event: "):
                        ev = line[7:]
                    elif line.startswith("data: "):
                        try:
                            d = json.loads(line[6:])
                        except ValueError:
                            d = line[6:]
                if ev:
                    events.append((ev, d, round(time.time() - t0, 1)))
                    if ev == "delta":
                        if t_first is None:
                            t_first = round(time.time() - t0, 1)
                        deltas.append(d.get("text", "") if isinstance(d, dict) else str(d))
    return events, "".join(deltas), t_first


def s_app(rec, t0):
    slug = re.sub(r"[^a-z0-9]+", "-", APP_PROMPT.lower()).strip("-")[:60]
    body = {"prompt": APP_PROMPT}
    if os.path.exists(f"/opt/vibeos/cache/apps/{slug}/app.html"):
        body["app_id"] = slug + "-vm0929"          # never a cached reopen: this must be a generation
    events, deltas, t_first = sse_app(body)
    kinds = [e for e, _, _ in events]
    rec.update(events=len(events), event_kinds=sorted(set(kinds)), first_delta_s=t_first,
               stream_notes=[l for l in deltas.splitlines() if l.startswith("//")][:14])
    done = [(d, t) for e, d, t in events if e == "done"]
    if not done:
        raise RuntimeError(f"no done event: {[(e, str(d)[:200]) for e, d, _ in events if e != 'delta'][:5]}")
    d, t = done[0]
    html = d.get("html", "")
    STATE["app_id"] = d.get("app_id") or body.get("app_id") or slug
    open(f"{OUT}/app-text-editor.html", "w").write(html)
    rec.update(seconds_to_done=t, app_id=STATE["app_id"], version=d.get("version"), html_chars=len(html),
               broken=d.get("broken"), gate_repair_in_stream=("self-repair" in deltas), gate_note=("// gate:" in deltas))
    g = sh(f"cd /opt/vibeos/os/kernel && python3 -c \"import json, jscheck; print(json.dumps(jscheck.js_syntax_problems(open('{OUT}/app-text-editor.html').read())))\"", timeout=120)
    rec["gate_on_delivered_html"] = g.stdout.strip() or g.stderr[-300:]
    low = html.lower()
    rec["needles"] = {n: (n in low) for n in ("find", "replace", "word", "undo", "save", "vibe.fs", "prompt(", "confirm(")}
    s, _ = api("GET", "/api/status")
    rec["loaded_after"] = s.get("loaded"); rec["llm"] = s.get("llm")
    rec["journal"] = journal_since(t0, r"\[kernel\]|llama-server|gate|repair", 25)
    assert d.get("broken") in (None, {}, ""), f"delivered flagged broken: {d.get('broken')}"


def image_job(rec, body, fname, timeout=1200):
    j, code = api("POST", "/api/image", body)
    assert code == 200 and isinstance(j, dict) and j.get("job"), (code, j)
    job = poll_job(j["job"], timeout)
    rec["job"] = {k: job.get(k) for k in ("status", "result", "prompt", "expanded", "expander", "seed", "engine", "error", "kind")}
    if job["status"] != "done":
        raise RuntimeError(job.get("error"))
    rec["png_bytes"] = fetch(job["result"], f"{OUT}/{fname}")
    rec["png"] = png_info(f"{OUT}/{fname}")
    STATE.setdefault("images", []).append(job["result"])


def s_image(rec, t0):
    image_job(rec, {"prompt": "a lighthouse on a rocky coast at dusk"}, "img-default.png")
    rec["journal"] = journal_since(t0, r"sd-server|expand|sd-cli|llama-server", 12)
    assert rec["job"]["expanded"], "default Krea 2 job was not expanded"


def s_images(rec, t0):
    prompts = ["a red bicycle leaning against a blue door", "a bowl of ramen on a wooden table, steam rising",
               "a snowy mountain village at night with warm windows"]
    j, code = api("POST", "/api/images", {"prompts": prompts})
    assert code == 200 and j.get("job"), (code, j)
    job = poll_job(j["job"], 2400)
    rec["job_status"] = job.get("status"); rec["urls"] = job.get("result")
    rec["records"] = [{k: r.get(k) for k in ("url", "expanded", "expander", "seed", "engine", "error")} for r in (job.get("results") or [])]
    if job["status"] != "done":
        raise RuntimeError(job.get("error"))
    rec["png"] = []
    for i, u in enumerate(job["result"] or []):
        if u:
            fetch(u, f"{OUT}/img-batch-{i + 1}.png"); rec["png"].append(png_info(f"{OUT}/img-batch-{i + 1}.png"))
    rec["journal"] = journal_since(t0, r"sd-server|session|expand", 14)
    assert len([u for u in job["result"] if u]) == 3, job["result"]


def s_genimg(rec, t0):
    addr = "news.kairos/today"
    path = "/web/" + addr
    html, code = api("GET", path, timeout=60)
    n = 0
    while isinstance(html, str) and "the imaginary web is" in html and time.time() - t0 < 1500:
        time.sleep(3); n += 1
        html, code = api("GET", f"{path}?_t={n}", timeout=60)
    rec["page_seconds"] = round(time.time() - t0, 1); rec["page_chars"] = len(html) if isinstance(html, str) else None
    open(f"{OUT}/genimg-page.html", "w").write(html if isinstance(html, str) else str(html))
    srcs = []
    for m in re.finditer(r'src="(/genimg/[^"]+)"', html if isinstance(html, str) else ""):
        u = m.group(1).replace("&amp;", "&")
        if u not in srcs:
            srcs.append(u)
    rec["pictures_on_page"] = len(srcs); rec["srcs"] = [urllib.parse.unquote(s)[:120] for s in srcs]
    t1 = time.time()

    def get(i_u):
        i, u = i_u
        data, c = api("GET", u, raw=True, timeout=900)
        dest = f"{OUT}/genimg-{i + 1}.bin"
        open(dest, "wb").write(data if isinstance(data, bytes) else str(data).encode())
        kind = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ("svg-placeholder" if b"<svg" in data[:400] else "other")
        return {"i": i + 1, "status": c, "bytes": len(data), "kind": kind, "s": round(time.time() - t1, 1)}
    with cf.ThreadPoolExecutor(max_workers=max(1, len(srcs))) as ex:     # a browser fetches them in parallel
        rec["fetches"] = list(ex.map(get, enumerate(srcs)))
    rec["pictures_seconds"] = round(time.time() - t1, 1)
    rec["journal"] = journal_since(t0, r"sd-server|session|genimg|web", 20)
    rec["sessions_logged"] = [l for l in rec["journal"] if "session of" in l]
    assert srcs, "the page has no /genimg pictures"
    assert all(f["kind"] == "png" for f in rec["fetches"]), rec["fetches"]


def s_uncensored(rec, t0):
    image_job(rec, {"prompt": "a portrait of a woman in a red dress on a balcony at golden hour", "model": "uncensored"},
              "img-uncensored-kroma.png")
    rec["journal"] = journal_since(t0, r"sd-server|expand|kroma|image_uncensored", 12)


def s_video(rec, t0):
    j, code = api("POST", "/api/video", {"prompt": "a paper boat drifting on a forest stream, soft morning light"})
    assert code == 200 and j.get("job"), (code, j)
    job = poll_job(j["job"], 2400)
    rec["job"] = {k: job.get(k) for k in ("status", "result", "error")}
    if job["status"] != "done":
        raise RuntimeError(job.get("error"))
    rec["mp4_bytes"] = fetch(job["result"], f"{OUT}/video.mp4"); rec["ffprobe"] = ffprobe(f"{OUT}/video.mp4")
    rec["journal"] = journal_since(t0, r"vid|sd-cli|video", 10)


def s_music(rec, t0):
    j, code = api("POST", "/api/music", {"prompt": "calm piano and soft strings, instrumental", "duration": 15})
    assert code == 200 and j.get("job"), (code, j)
    job = poll_job(j["job"], 1200)
    rec["job"] = {k: job.get(k) for k in ("status", "result", "error")}
    if job["status"] != "done":
        raise RuntimeError(job.get("error"))
    rec["wav_bytes"] = fetch(job["result"], f"{OUT}/music.wav"); rec["ffprobe"] = ffprobe(f"{OUT}/music.wav")


def s_game(rec, t0):
    rec["games_before"] = sorted(os.listdir("/opt/vibeos/games")) if os.path.isdir("/opt/vibeos/games") else []
    j, code = api("POST", "/api/game", {"prompt": "snake"})
    assert code == 200 and j.get("job"), (code, j)
    job = poll_job(j["job"], 2400)
    rec["job"] = {k: job.get(k) for k in ("status", "result", "error")}
    if job["status"] != "done":
        raise RuntimeError(str(job.get("error"))[-600:])
    w, _ = api("GET", "/api/windows")
    rec["windows"] = w if not isinstance(w, dict) else {k: w.get(k) for k in ("available", "wm", "screens", "windows")}
    STATE["game_window"] = (job.get("result") or {}).get("window")
    rec["journal"] = journal_since(t0, r"game|python_llm|llama", 12)
    assert (job.get("result") or {}).get("running"), job.get("result")


def s_vision(rec, t0):
    src = (STATE.get("images") or ["/files/images/" + sorted(os.listdir("/opt/vibeos/cache/files/images"))[-1]])[0]
    rec["image"] = src
    t1 = time.time()
    j, code = api("POST", "/api/vision", {"image": src, "prompt": "Describe this image in one sentence."}, timeout=900)
    rec.update(http=code, seconds_call=round(time.time() - t1, 1), answer=(j.get("text") if isinstance(j, dict) else str(j))[:600],
               error=(j.get("error") if isinstance(j, dict) else None))
    rec["journal"] = journal_since(t0, r"vision|llama-server", 8)
    assert code == 200 and j.get("text"), (code, j)


FN = {"status": s_status, "app": s_app, "image": s_image, "images": s_images, "genimg": s_genimg,
      "uncensored": s_uncensored, "video": s_video, "music": s_music, "game": s_game, "vision": s_vision}
log(f"feature test start: steps {STEPS}; GPU {sh('nvidia-smi --query-gpu=memory.used --format=csv,noheader').stdout.strip()}")
for name in STEPS:
    step(name, FN[name])
VRAM.on = False
log("feature test end")
