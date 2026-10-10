#!/usr/bin/env python3
"""Report which files of a tier manifest are fully present in the models dir — and list
the BAKED set, i.e. exactly the files that belong into a shipped image [R47].

    check_tier_present.py <manifest> [models_dir] [--all] [--list] [--bytes]

Add-on sections (`"addon": true` — the extra image models, video, music, vision, the fast
LLM) are downloaded in-OS on demand and are NOT part of the baked set: without --all they
are skipped, so a build host that lacks an add-on still passes and an add-on that happens
to be on the build host is never copied into the image (docs/FIXES-2026-09-28.md A3).
EXCEPT the bake-flagged ones (docs/IMAGE-BAKE-ADDONS-2026-10-07.md): `"bake": true` on an
add-on section (every file of it) or on one {url,file} entry inside one (that file only)
means "ship it in the image although it stays an add-on" — today the translation layer's
`fast` model and the `dictionaries` archives [R71], so translation and word lookup work
offline on the stick. Those files are part of --list, --bytes and the presence gate exactly
like the non-addon files (the build paths' exit 90/91 cover them); the report marks them
"baked add-on". The kernel's tier presence (kernel/server.py manifest_files) keeps ignoring
every add-on, baked or not — a baked file only makes its add-on report present. A `store:
"knowledge"` entry (a Wiktionary ZIM) is never listed, flag or not: it lives in knowledge/.
--list prints one relative path per line (the input for rsync --files-from) and nothing else.
--bytes prints the total size in bytes of the set as it will be COPIED — symlinks resolved —
and nothing else on stdout; exit 1 (offenders on stderr) when a file is missing, partial or a
dangling link. Symlinks: a models dir made of links is fine (the build paths copy the targets:
rsync -L / shutil.copy2 on the realpath), but a DANGLING link used to pass unnoticed (it only
counted as missing) and a valid link was sized as its 516-byte link by `du` without -L; now a
dangling link is reported as DANGLING and fails the gate, and sizes always follow the link
(docs/FIXES-2026-09-29.md, the symlink risk of the re-verification).
"""
import json
import os
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {a for a in sys.argv[1:] if a.startswith("--")}
if not args:
    sys.exit(__doc__)
manifest = args[0]
models = args[1] if len(args) > 1 else "/opt/vibeos/models"
include_addons = "--all" in flags
m = json.load(open(manifest))
baked = []                                   # add-on files in the set because of "bake": true


def has_bake(n):
    return isinstance(n, dict) and (bool(n.get("bake")) or any(has_bake(v) for v in n.values()))


def walk(n, out, addon=False, bake=False):
    if isinstance(n, dict):
        if n.get("store") == "knowledge":          # a ZIM add-on lives in the knowledge dir, never in models/ [R71 P3]
            return
        if n.get("addon") and not include_addons and not has_bake(n):
            return                                  # an add-on nobody flagged: downloaded in-OS, never baked
        addon = addon or bool(n.get("addon"))
        bake = bake or bool(n.get("bake"))          # inherited: a flag on the section covers every file of it
        if "file" in n and "url" in n:
            if addon and not include_addons and not bake:
                return                              # the unflagged file of a partly baked add-on (the quality 9B)
            if addon and bake:
                baked.append(n["file"])
            out.append(n["file"])
        for v in n.values():
            walk(v, out, addon, bake)


def status(p):
    """(present, size_bytes, note) — sizes follow symlinks; a dangling link is its own state"""
    if os.path.islink(p) and not os.path.exists(p):
        return False, 0, "DANGLING"
    if not os.path.exists(p):
        return False, 0, "MISSING"
    size = os.path.getsize(p)                   # follows the link: the bytes a copy will take
    if os.path.exists(p + ".aria2"):
        return False, size, "PARTIAL"
    return True, size, "OK"


files = []
walk(m, files)
files = list(dict.fromkeys(files))          # shared files (an encoder used by two sections) once
baked = [f for f in dict.fromkeys(baked) if f in files]
if "--list" in flags:
    print("\n".join(files))
    sys.exit(0)
if "--bytes" in flags:
    total, bad = 0, []
    for f in files:
        ok, size, note = status(os.path.join(models, f))
        total += size
        if not ok:
            bad.append(f"{note} {f}")
    if bad:
        print("\n".join(bad), file=sys.stderr)
        sys.exit(1)
    print(total)
    sys.exit(0)
total = 0.0
all_ok = True
for f in files:
    ok, size, note = status(os.path.join(models, f))
    total += size / 1e9
    all_ok = all_ok and ok
    print(f"  {note:7s} {f}  {size / 1e9:.1f}GB" + ("  (baked add-on)" if f in baked else ""))
mode = "with add-ons" if include_addons else \
    ("without add-ons" + (f", +{len(baked)} baked add-on files" if baked else ""))
print(f"  total {total:.1f}GB ({mode})  ->  {'ALL PRESENT' if all_ok else 'INCOMPLETE'}")
sys.exit(0 if all_ok else 1)
