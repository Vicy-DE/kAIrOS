#!/usr/bin/env bash
# Build the distributable vibeOS-local WSL image.
#
#   build-image.sh [tier] [output-dir]      (tier defaults to 8gb — the embedded set)
#
# Produces vibeos-local-<tier>.wsl — a complete, slim Debian rootfs with:
#   - llama.cpp + stable-diffusion.cpp (CUDA) binaries, kiwix-serve
#   - the vibeOS kernel + shell + ALL tier manifests
#   - the chosen tier's models BAKED IN (default: 8gb starter set)
# At first boot the OS detects the GPU's VRAM and offers to download a bigger
# tier in-OS (Settings app can do it later too) — so one 8gb image fits everyone.
# Import on any Windows 11 machine with an NVIDIA GPU:
#   wsl --import vibeos <install-dir> vibeos-local-<tier>.wsl
#   wsl -d vibeos   ->  open http://localhost:1337
#
# Must run as root inside the build WSL (engines + that tier's models already present).
set -euo pipefail

TIER="${1:-8gb}"
OUTDIR="${2:-$(cd "$(dirname "$0")/.." && pwd)/dist}"
REPO=$(cd "$(dirname "$0")/.." && pwd)
MANIFEST=$REPO/manifests/tier-$TIER.json
VIBEOS=/opt/vibeos
ROOT=/srv/vibeos-rootfs-$TIER

[ -f "$MANIFEST" ] || { echo "no manifest for tier $TIER"; exit 1; }
command -v debootstrap >/dev/null || apt-get install -y -qq debootstrap

echo "== [1/6] minimal Debian rootfs =="
if [ ! -f "$ROOT/etc/os-release" ]; then
    rm -rf "$ROOT"
    debootstrap --variant=minbase --include=ca-certificates trixie "$ROOT" http://deb.debian.org/debian
fi

echo "== [2/6] runtime packages =="
chroot "$ROOT" bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-aiohttp ffmpeg python3-pygame libcurl4 libgomp1 aria2 ca-certificates quickjs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*"
# quickjs: the kernel's JavaScript syntax gate parses every generated app before delivery
# (kernel/jscheck.py, docs/FIXES-2026-09-28.md D7)

echo "== [3/6] engines + CUDA runtime libs =="
mkdir -p "$ROOT$VIBEOS/bin" "$ROOT/usr/local/cuda/lib64"
# resolved binaries (deref the symlinks)
cp -L $VIBEOS/bin/llama-server $VIBEOS/bin/llama-cli $VIBEOS/bin/sd-cli "$ROOT$VIBEOS/bin/"
[ -x $VIBEOS/bin/kiwix-serve ] && cp -L $VIBEOS/bin/kiwix-serve "$ROOT$VIBEOS/bin/"
# music engine [R31] + the Vulkan (AMD iGPU) engine set [R30], when they have been built
for b in ace-lm ace-synth ace-server sd-server; do [ -e "$VIBEOS/bin/$b" ] && cp -L "$VIBEOS/bin/$b" "$ROOT$VIBEOS/bin/"; done
if [ -d "$VIBEOS/bin/vulkan" ]; then mkdir -p "$ROOT$VIBEOS/bin/vulkan"; cp -L "$VIBEOS/bin/vulkan/"* "$ROOT$VIBEOS/bin/vulkan/"; fi
# the engine stamp (install-engines.sh -> engine-stamp.py) ships with the binaries: the kernel
# refuses an unstamped set as older than every manifest requirement, so its absence is a build error
[ -f "$VIBEOS/bin/ENGINES.json" ] || { echo "no $VIBEOS/bin/ENGINES.json — run scripts/engine-stamp.py --root $VIBEOS (install-engines.sh writes it)"; exit 1; }
cp "$VIBEOS/bin/ENGINES.json" "$ROOT$VIBEOS/bin/"
# shared libs the binaries need (cublas/cudart from the build toolkit)
for bin in "$ROOT$VIBEOS/bin/"*; do
    ldd "$bin" 2>/dev/null | awk '/=> \// {print $3}' | grep -Ev '^/usr/lib/wsl' | sort -u
done | sort -u | while read -r lib; do
    rel=${lib#/}
    if [ ! -e "$ROOT/$rel" ]; then
        mkdir -p "$ROOT/$(dirname "$rel")"
        cp -L "$lib" "$ROOT/$rel"
    fi
done
# ggml backends are dlopened, ldd misses them
# (libmtmd added 2026-07-02: llama-server links it — its absence made the LLM
#  engine unable to start in the shipped image; caught by the GPU testrig)
find $VIBEOS/engines -name 'libggml*.so*' -o -name 'libllama*.so*' -o -name 'libstable-diffusion*.so*' -o -name 'libmtmd*.so*' 2>/dev/null \
    | while read -r lib; do cp -L "$lib" "$ROOT/usr/local/lib/" 2>/dev/null || true; done
echo "/usr/local/lib" > "$ROOT/etc/ld.so.conf.d/vibeos.conf"
echo "/usr/lib/wsl/lib" >> "$ROOT/etc/ld.so.conf.d/vibeos.conf"
chroot "$ROOT" ldconfig || true

echo "== [4/6] vibeOS kernel + shell =="
mkdir -p "$ROOT$VIBEOS/os"
cp -r "$REPO/kernel" "$REPO/shell" "$REPO/scripts" "$REPO/manifests" "$REPO/docs" "$ROOT$VIBEOS/os/"
cp "$MANIFEST" "$ROOT$VIBEOS/manifest.json"
echo "$TIER" > "$ROOT$VIBEOS/active-tier"

echo "== [5/6] baking tier models (the non-addon set + the bake-flagged add-ons, as a MIRROR of the bake list) =="
# exactly the files that are not "addon": true — add-ons (extra image models, video, music,
# vision, the fast LLM) are downloaded in-OS on demand [R47] — plus the bake-flagged add-on
# files ("bake": true: the translation `fast` model + the dictionaries [R71],
# docs/IMAGE-BAKE-ADDONS-2026-10-07.md); scripts/check_tier_present.py
# --list is the one definition of that set (docs/FIXES-2026-09-28.md A3). The build root is
# REUSED between builds ([1/6] keeps it), so the target models dir is made a mirror of the list:
# whatever an earlier build baked that the list no longer names is deleted — the re-verification
# found a pre-fix root would still have packed 18 add-on files / 54 GiB (N2, docs/FIXES-2026-09-29.md).
# Deletion happens only inside <build root>/opt/vibeos/models (guarded). Symlinked sources are
# copied as their targets; a dangling link fails the build.
python3 - "$MANIFEST" "$VIBEOS/models" "$ROOT" "$ROOT$VIBEOS/models" "$REPO/scripts/check_tier_present.py" <<'PY'
import os, shutil, subprocess, sys
manifest, src_dir, root, dst_dir, lister = sys.argv[1:6]
root, dst_dir, src_dir = os.path.abspath(root), os.path.abspath(dst_dir), os.path.abspath(src_dir)
# the mirror DELETES — only ever inside the build root's own models directory, never the source
if root == "/" or not dst_dir.startswith(root + "/") or not dst_dir.endswith("/opt/vibeos/models") \
        or os.path.realpath(dst_dir) == os.path.realpath(src_dir):
    sys.exit(f"refusing to mirror into {dst_dir} (must be <build root>/opt/vibeos/models and not the source dir)")
r = subprocess.run([sys.executable, lister, manifest, src_dir, "--list"], capture_output=True, text=True)
files = r.stdout.split()
if r.returncode != 0 or not files:
    sys.exit(f"could not list the baked set of {manifest}: {r.stderr.strip()}")
for f in files:
    src, dst = os.path.join(src_dir, f), os.path.join(dst_dir, f)
    if not os.path.exists(src):
        sys.exit(f"MISSING MODEL: {src}" + (" (dangling symlink)" if os.path.islink(src) else "") + " — download it first")
    real = os.path.realpath(src)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.islink(dst) or (os.path.exists(dst) and os.path.getsize(dst) != os.path.getsize(real)):
        os.remove(dst)                                   # a link or a stale/partial copy: redo it
    if not os.path.exists(dst):
        print(f"  baking {f}", flush=True); shutil.copy2(real, dst)   # the target's bytes, never a link
keep = {os.path.normpath(os.path.join(dst_dir, f)) for f in files}
for dp, dns, fns in os.walk(dst_dir, topdown=False):
    for fn in fns:
        p = os.path.join(dp, fn)
        if os.path.normpath(p) not in keep:
            print(f"  removing stale {os.path.relpath(p, dst_dir)} ({os.lstat(p).st_size // 1048576} MiB)", flush=True)
            os.remove(p)
    for dn in dns:
        try:
            os.rmdir(os.path.join(dp, dn))               # only when empty after the removals
        except OSError:
            pass
print(f"  mirrored {len(files)} files: {', '.join(files)}")
PY

echo "== [5b/6] baking knowledge base =="
if ls $VIBEOS/knowledge/*.zim >/dev/null 2>&1; then
    mkdir -p "$ROOT$VIBEOS/knowledge"
    cp -n $VIBEOS/knowledge/*.zim "$ROOT$VIBEOS/knowledge/"
else
    echo "  (no ZIM present — image ships without offline knowledge)"
fi

echo "== [6/6] boot config + export =="
cat > "$ROOT/etc/wsl.conf" <<'EOF'
[boot]
command = "mkdir -p /opt/vibeos/cache && nohup python3 /opt/vibeos/os/kernel/server.py >> /opt/vibeos/kernel.log 2>&1 &"

[user]
default = root

[network]
hostname = kairos
EOF
cat > "$ROOT$VIBEOS/os/BOOT.txt" <<'EOF'
kAIrOS by mAIer — import with:
  wsl --import kairos C:\wsl\kairos kairos-<tier>.wsl --version 2
  wsl -d kairos
then open http://localhost:1337
EOF
# environment defaults for the kernel — VIBEOS_MANIFEST deliberately NOT set:
# the kernel resolves the tier itself (active-tier file, updated by in-OS upgrades)
mkdir -p "$ROOT/etc/profile.d"
cat > "$ROOT/etc/profile.d/vibeos.sh" <<EOF
export VIBEOS_DIR=$VIBEOS
export VIBEOS_SHELL=$VIBEOS/os/shell
export VIBEOS_MANIFESTS=$VIBEOS/os/manifests
EOF
sed -i 's|command = "|command = "export VIBEOS_DIR=/opt/vibeos VIBEOS_SHELL=/opt/vibeos/os/shell VIBEOS_MANIFESTS=/opt/vibeos/os/manifests; |' "$ROOT/etc/wsl.conf"

mkdir -p "$OUTDIR"
OUT="$OUTDIR/kairos-$TIER.wsl"
echo "packing $OUT (plain tar — models don't compress)"
tar -C "$ROOT" --numeric-owner -cf "$OUT" .
echo "IMAGE BUILD COMPLETE: $OUT ($(du -h "$OUT" | cut -f1))"
