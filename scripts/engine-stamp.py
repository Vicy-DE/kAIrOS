#!/usr/bin/env python3
"""Write the engine stamp — bin/ENGINES.json — next to a built engine set.

The manifests' argv is validated against ONE engine set (docs/ENGINES-CUDA-2026-09.md §5.2:
sd.cpp 7f410a3's `--auto-fit` made `--offload-to-cpu` a failure on the 8 GB 4070, so the
flag left the manifests — and a July sd-cli paired with today's manifests would OOM with a
misleading error). The stamp records what was built so the kernel can refuse a pairing the
manifests were never validated with (kernel/server.py check_engines) instead of crashing an
engine. Written by scripts/install-engines.sh at the end of every build; shipped by
iso/build-iso.sh and scripts/build-image.sh next to the binaries.

    engine-stamp.py [--root /opt/vibeos] [--backend cuda|vulkan] [--out PATH]
    engine-stamp.py --mock --out /tmp/kairos-test/bin/ENGINES.json     (the test suite)

Ordering key: each engine's COMMIT DATE (`committed`, ISO 8601 UTC), the only monotonic id
all three upstreams expose from the `--depth 1` clones the build makes — llama.cpp's
LLAMA_BUILD_NUMBER is 0 in a shallow clone (build/common/build-info.cpp), sd.cpp's
`master-NNN-<hash>` tag exists only when upstream CI had tagged HEAD at clone time, and
acestep.cpp has no tags at all. `describe` and the parsed `build` number are recorded for
humans, the date is what the manifests' `engines` field is compared against.
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

STAMP_NAME = "ENGINES.json"
STAMP_FORMAT = 1
# upstream tree name -> the binaries the kernel execs from it
ENGINES = {
    "llama.cpp": ["llama-server", "llama-cli"],
    # sd-server (examples/server, built by default since 2026-09) keeps one image model loaded
    # for a whole batch — the kernel's multi-image sessions (docs/INTEGRATION-2026-09-28.md);
    # sd-cli stays the per-job / video path and the fallback for a set without the server
    "stable-diffusion.cpp": ["sd-cli", "sd-server"],
    "acestep.cpp": ["ace-lm", "ace-synth", "ace-server"],
}


def git(tree: Path, *args) -> str:
    # the build runs as root and the artefact dirs are then read by another uid: never
    # let git's "dubious ownership" guard turn a stamp into an empty one
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", str(tree), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def build_number(describe: str):
    """The monotonic build number upstream tags carry: llama.cpp `b10931`,
    sd.cpp `master-859-7f410a3`; None when the description is a bare hash."""
    m = re.fullmatch(r"b(\d+)", describe) or re.match(r"master-(\d+)-[0-9a-f]+$", describe)
    return int(m.group(1)) if m else None


def utc(iso: str) -> str:
    """`git log --format=%cI` (offset-aware) -> `YYYY-MM-DDTHH:MM:SSZ`."""
    d = dt.datetime.fromisoformat(iso)
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp_engine(tree: Path, binaries: list) -> dict:
    full, committed = git(tree, "log", "-1", "--format=%H%n%cI").split("\n")
    describe = git(tree, "describe", "--tags", "--always")
    return {"commit": full[:7], "commit_full": full, "committed": utc(committed),
            "describe": describe, "build": build_number(describe), "binaries": binaries}


def cuda_info(build_dir: Path) -> dict:
    """CUDA arch + toolkit version from the CMake tree (None when not a CUDA build)."""
    arch = version = None
    cache = build_dir / "CMakeCache.txt"
    if cache.exists():
        m = re.search(r"^CMAKE_CUDA_ARCHITECTURES:\w+=(.+)$", cache.read_text(errors="ignore"), re.M)
        arch = m.group(1).strip() if m else None
    for f in build_dir.glob("CMakeFiles/*/CMakeCUDACompiler.cmake"):
        m = re.search(r'CMAKE_CUDA_COMPILER_VERSION "([^"]+)"', f.read_text(errors="ignore"))
        if m:
            version = m.group(1)
    return {"cuda_arch": arch, "cuda": version}


def make_stamp(root: Path, backend: str) -> dict:
    build_dir = "build-vulkan" if backend == "vulkan" else "build"
    stamp = {"format": STAMP_FORMAT, "backend": backend,
             "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "cuda_arch": None, "cuda": None, "engines": {}}
    for name, binaries in ENGINES.items():
        tree = root / "engines" / name
        if not (tree / ".git").exists():
            continue                                   # not built in this set
        stamp["engines"][name] = stamp_engine(tree, binaries)
        if backend == "cuda" and name == "llama.cpp":
            stamp.update(cuda_info(tree / build_dir))
    if not stamp["engines"]:
        sys.exit(f"engine-stamp: no engine source trees under {root / 'engines'}")
    return stamp


def mock_stamp() -> dict:
    """The test suite's mock engines: an explicit stamp dated now, so the kernel's check
    is exercised (and satisfied) rather than bypassed by an absent file."""
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"format": STAMP_FORMAT, "backend": "mock", "built": now, "cuda_arch": None, "cuda": None,
            "engines": {name: {"commit": "mock", "commit_full": "mock", "committed": now,
                               "describe": "mock", "build": None, "binaries": bins}
                        for name, bins in ENGINES.items()}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default="/opt/vibeos", help="VIBEOS dir holding engines/ and bin/")
    ap.add_argument("--backend", choices=("cuda", "vulkan"), default="cuda")
    ap.add_argument("--out", help="stamp path (default <root>/bin[/vulkan]/ENGINES.json)")
    ap.add_argument("--mock", action="store_true", help="stamp for the suite's mock engines")
    a = ap.parse_args()
    root = Path(a.root)
    stamp = mock_stamp() if a.mock else make_stamp(root, a.backend)
    out = Path(a.out) if a.out else root / "bin" / ("vulkan" if a.backend == "vulkan" else "") / STAMP_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stamp, indent=1) + "\n")
    eng = ", ".join(f"{n} {e['commit']} ({e['committed'][:10]}{', ' + e['describe'] if e['describe'] != e['commit'] else ''})"
                    for n, e in stamp["engines"].items())
    print(f"engine stamp {out}: {stamp['backend']} — {eng}")


if __name__ == "__main__":
    main()
