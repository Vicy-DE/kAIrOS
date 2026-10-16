#!/usr/bin/env python3
"""Download the BAKED model set of a tier manifest — the files an image ships (R47) — and nothing else.

    download-baked-models.py <manifest.json> [models_dir] [--all] [--list] [--check]

The list comes from scripts/check_tier_present.py --list (non-addon files + bake-flagged add-ons;
knowledge-store ZIMs never), so this and the image build agree on what "baked" means. --all adds
every add-on of the manifest except the knowledge-store ZIMs (they belong under knowledge/,
scripts/download-knowledge.sh territory). --list prints the plan and exits; --check verifies the
sha256 of present files only.

Downloads are resumable: aria2c when installed, else curl -C -. A file that already has the
manifest's size_bytes (or size_gb within 2 %) is skipped; sha256 mismatches are reported at the end
and leave the file in place for you to decide. Some files cannot be fetched anonymously (the
manifest comment says so, e.g. a civitai login) — they are listed as MANUAL.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def usage(code=2):
    print(__doc__.strip(), file=sys.stderr)
    sys.exit(code)


args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {a for a in sys.argv[1:] if a.startswith("--")}
if not args or flags - {"--all", "--list", "--check"}:
    usage()
manifest_path = Path(args[0])
models_dir = Path(args[1]) if len(args) > 1 else Path("/opt/vibeos/models")
manifest = json.loads(manifest_path.read_text())


def entries(node, addon=False, bake=False, out=None):
    """every {url,file} entry with the add-on / bake flags of its enclosing sections"""
    if out is None:
        out = []
    if isinstance(node, dict):
        addon = addon or bool(node.get("addon"))
        bake = bake or bool(node.get("bake"))
        if "url" in node and "file" in node:
            out.append((node, addon, bake))
        for v in node.values():
            entries(v, addon, bake, out)
    elif isinstance(node, list):
        for v in node:
            entries(v, addon, bake, out)
    return out


lister = REPO / "scripts" / "check_tier_present.py"
baked = set(subprocess.run([sys.executable, str(lister), str(manifest_path), str(models_dir), "--list"],
                           capture_output=True, text=True, check=True).stdout.split())
plan = []
for e, addon, bake in entries(manifest):
    if e.get("store") == "knowledge":
        continue
    if e["file"] in baked or ("--all" in flags and addon):
        if e["file"] not in {p["file"] for p in plan}:
            plan.append(e)

total_gb = sum(e.get("size_gb", 0) for e in plan)
print(f"[baked] {len(plan)} files, ~{total_gb:.1f} GB -> {models_dir}", flush=True)
if "--list" in flags:
    for e in plan:
        print(f"  {e.get('size_gb', '?'):>6} GB  {e['file']}  <- {e['url']}")
    sys.exit(0)


def present(dest, e):
    if not dest.exists():
        return False
    size = dest.stat().st_size
    if isinstance(e.get("size_bytes"), int):
        return size == e["size_bytes"]
    expected = e.get("size_gb", 0) * 1e9
    return bool(expected) and abs(size - expected) / expected < 0.02


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("aria2c"):
        cmd = ["aria2c", "-x8", "-s8", "--continue=true", "--summary-interval=30", "--console-log-level=warn",
               "--file-allocation=none", "-d", str(dest.parent), "-o", dest.name, url]
    else:
        cmd = ["curl", "-fL", "-C", "-", "--retry", "5", "--retry-all-errors", "--speed-limit", "2048",
               "--speed-time", "60", "-o", str(dest), url]
    return subprocess.run(cmd).returncode == 0


failed, manual, mismatched = [], [], []
for e in plan:
    dest = models_dir / e["file"]
    if present(dest, e):
        print(f"[skip] {e['file']} (present)", flush=True)
    elif "--check" in flags:
        print(f"[miss] {e['file']}", flush=True)
        continue
    else:
        comment = (e.get("comment") or "") + " " + (e.get("note") or "")
        if "login" in comment.lower() or "civitai.com" in e["url"]:
            manual.append(e)
            print(f"[MANUAL] {e['file']}: needs a logged-in download from {e['url']} into {dest.parent}", flush=True)
            continue
        print(f"[get ] {e['file']} ({e.get('size_gb', '?')} GB)", flush=True)
        if not fetch(e["url"], dest):
            failed.append(e["file"])
            continue
    if e.get("sha256") and dest.exists():
        got = sha256(dest)
        if got != e["sha256"].lower():
            mismatched.append((e["file"], got))

for f, got in mismatched:
    print(f"[SHA256 MISMATCH] {f}: {got} (manifest {dict((e['file'], e.get('sha256')) for e in plan)[f]})")
if manual:
    print(f"[baked] {len(manual)} file(s) need a manual download (see MANUAL lines above)")
if failed:
    print(f"[baked] DONE WITH ERRORS — failed: {failed}")
    sys.exit(1)
print("[baked] " + ("CHECK COMPLETE" if "--check" in flags else "ALL DOWNLOADS COMPLETE") + (" (with mismatches)" if mismatched else ""))
sys.exit(1 if mismatched else 0)
