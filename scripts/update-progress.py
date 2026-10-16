#!/usr/bin/env python3
"""Regenerate docs/PROGRESS.md from docs/REQUIREMENTS.md + tests/results.json [R26].

The table is driven by REQUIREMENTS.md: every `**R<N> — title**` in the document
becomes a row — a requirement can not silently vanish from the progress report.

"Tested" is derived purely from the latest test run (results.json maps each test
to the requirement IDs it was decorated with in tests/test_kernel.py).

"Implemented" is derived, in this order:
  * LOCUS       — a human-named implementation locus (small map below; only for
                  requirements where naming the code adds information — mostly
                  the ones no automated test covers);
  * EXTERNAL    — implemented outside this repository (unauditable from a checkout);
  * PARTIAL     — a known-incomplete implementation (the note says what is missing);
  * by test     — no named locus, but a passing automated test asserts it;
  * ❓ unclaimed — nothing names it and no test covers it. Deliberately visible.

Requirement annotations written in REQUIREMENTS.md as
`**Status note (date):** …` are copied into the Notes column, so supersession /
not-implemented notes live in ONE place (the requirements document) and flow here.

Run via tests/run-tests.sh (inside the testrig container) or directly after a run.
"""
import json
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REQ_DOC = REPO / "docs" / "REQUIREMENTS.md"
RESULTS = REPO / "tests" / "results.json"
OUT = REPO / "docs" / "PROGRESS.md"

# requirement id -> implementation locus. Keep this SMALL: name code only where the
# tests do not already prove the requirement, or where the locus is non-obvious.
LOCUS = {
    "R26": "docs/REQUIREMENTS.md + this script + tests/run-tests.sh + .claude/workflows/kairos-verify.js",
    "R32": "manifests/tier-*.json knowledge (wikipedia_en_all_mini, ~12 GB, every tier)",
    "R41": "iso/config/includes.chroot: usr/local/sbin/kairos-gpu-setup + nvidia-load.service (boot_vga / nvidia-drm modeset; installed by iso/build-img.sh + iso/patch-img.sh)",
    "R46": "shell/index.html dock has no 📖 app; docs stay at /api/docs + vibe.docs()",
    "R49": "docs/API.md 'WiFi connect — full reference' + docs/api/network.md",
    "R50": "iso/config/package-lists/kairos.list.chroot (firmware-intel-graphics, firmware-iwlwifi, intel-microcode)",
    "R52": "iso/config/includes.chroot/home/kairos/.xinitrc (one Chromium per xrandr geometry, ?screen=N, relaunch loop)",
    "R61": "shell/shell.js addTask()/#taskbar chips [R61]",
}

# implemented outside this repository — cannot be audited or reproduced from a checkout
EXTERNAL = {
    "R64": "pod/testrig/vm.sh (outside this repo)",
    "R65": "pod/testrig/gpu-display-setup.sh + run-gpu-vm.sh (outside this repo)",
}

# known-incomplete implementations: the note states what is missing
PARTIAL = {
    # R31 left this list on 2026-09-12: acestep.cpp built (Vulkan) and /api/music rendered a
    # real, non-silent WAV on the Radeon 780M through the kernel (docs/ENGINES-VULKAN-2026-09.md)
}

# requirements whose full proof needs hardware/integration beyond the automated suite
MANUAL_NOTES = {
    "R2": "full proof = real engines on GPU",
    "R3": "manifests tested; baking tiers into images = manual build step",
    "R4": "mock-tested; real render = tests/real-image-smoke.sh on the image",
    "R6": "mock-tested; video is an add-on since R47",
    "R7": "mock-tested (no-X survival fallback + fake-X mapped-window gate); a real window = kiosk X display on the image",
    "R8": "builder script static-checked; full image build = manual",
    "R11": "retry loop + a forced crash + the post-success crash record are mock-tested; a real model's repairs = hardware",
    "R13": "prompt mandate tested; visual look = human judgement",
    "R18": "API tested; real tier download = manual/e2e",
    "R30": "package/unit presence tested; inference on the Radeon 780M = hardware",
    "R33": "unit files tested; UVM/GTT behaviour = hardware",
    "R48": "targets API tested; a real install = manual on hardware",
    "R51": "unit wiring tested; a captured latest.txt = hardware boot",
    "R66": "bind/token/sshd/wifi config tested; a key-only image = build + boot",
}

REQ_RE = re.compile(r"\*\*(R\d+) — ([^*]+)\*\*")
# a status note is one sub-bullet: it runs until the next bullet or a blank line
NOTE_RE = re.compile(r"\*\*Status note[^*]*\*\*:?\s*(.+?)(?=\n\s*-\s|\n\s*\n|\Z)", re.S)
NOTE_CAP = 300


def parse_requirements(text: str):
    """-> (ordered list of ids, {id: title}, {id: status note})."""
    ids, titles, notes = [], {}, {}
    matches = list(REQ_RE.finditer(text))
    for i, m in enumerate(matches):
        rid = m.group(1)
        if rid in titles:          # a duplicated heading is a doc bug — surface it
            raise SystemExit(f"REQUIREMENTS.md defines {rid} twice")
        ids.append(rid)
        titles[rid] = m.group(2).strip().rstrip(":")
        block = text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        n = NOTE_RE.search(block)
        if n:
            note = re.sub(r"\s+", " ", n.group(1)).strip()
            notes[rid] = note if len(note) <= NOTE_CAP else note[:NOTE_CAP].rstrip() + " …"
    return ids, titles, notes


def numbering_holes(ids):
    nums = sorted(int(r[1:]) for r in ids)
    return [f"R{i}" for i in range(1, nums[-1] + 1) if i not in nums] if nums else []


def load_results(path: Path = RESULTS):
    if not path.exists():
        return "never", {}
    data = json.loads(path.read_text())
    return data.get("at", "?"), data.get("tests", {})


def render(ids, titles, notes, results, ran_at="?"):
    """Build the PROGRESS.md lines + the counters. Pure: no I/O."""
    per_req = {}
    for name, r in results.items():
        for req in r["reqs"]:
            per_req.setdefault(req, []).append((name, r["status"]))
    known = set(ids)
    unknown_refs = sorted({q for q in per_req if q not in known}, key=lambda x: int(x[1:]))
    holes = numbering_holes(ids)

    lines = [
        "# kAIrOS — Progress",
        "",
        "_Auto-generated by `scripts/update-progress.py` from `docs/REQUIREMENTS.md` +",
        "`tests/results.json` — do not edit by hand._",
        f"_Last test run: **{ran_at}** (suite: `tests/test_kernel.py` via `tests/run-tests.sh`,",
        "mock engines, forced-offline mode)._",
        "",
        "Legend — Implemented: ✅ done (named locus, or asserted by a passing test) ·",
        "⚠️ partial (note says what is missing) · 🔗 external (lives outside this repo) ·",
        "❓ unclaimed (no locus named, no test). Tested: 🧪 covered by passing automated",
        "test(s) · ⏭ test skipped (dependency not installed) · ❌ failing · ➖ not automated.",
        "",
    ]
    if holes or unknown_refs:
        lines.append("> **Traceability warnings**")
        if holes:
            lines.append(f"> - REQUIREMENTS.md skips id(s): {', '.join(holes)}")
        if unknown_refs:
            lines.append(f"> - tests reference id(s) not in REQUIREMENTS.md: {', '.join(unknown_refs)}")
        lines.append("")
    lines += [
        "| ID | Requirement | Implemented | Tested | Notes |",
        "|----|-------------|:-----------:|:------:|-------|",
    ]

    c = {"total": len(ids), "impl": 0, "locus": 0, "by_test": 0, "partial": 0,
         "external": 0, "unclaimed": 0, "tested": 0, "untested": 0, "fail": 0, "skip": 0}
    for rid in sorted(ids, key=lambda x: int(x[1:])):
        tests = per_req.get(rid, [])
        statuses = [s for _, s in tests]
        passing = [n for n, s in tests if s == "pass"]
        if not tests:
            tested = "➖"
            c["untested"] += 1
        elif "fail" in statuses:
            tested = "❌ " + ", ".join(n for n, s in tests if s == "fail")
            c["fail"] += 1
        elif all(s == "skip" for s in statuses):
            tested = "⏭ " + ", ".join(n for n, _ in tests)
            c["skip"] += 1
        else:
            tested = "🧪 " + ", ".join(passing)
            c["tested"] += 1

        note_parts = []
        if rid in PARTIAL:
            impl = "⚠️ partial"
            note_parts.append(PARTIAL[rid])
            c["partial"] += 1
        elif rid in EXTERNAL:
            impl = f"🔗 external `{EXTERNAL[rid]}`"
            c["external"] += 1
        elif rid in LOCUS:
            impl = f"✅ `{LOCUS[rid]}`"
            c["impl"] += 1
            c["locus"] += 1
        elif passing:
            impl = "✅ by test"
            c["impl"] += 1
            c["by_test"] += 1
        else:
            impl = "❓ unclaimed"
            c["unclaimed"] += 1
        if rid in MANUAL_NOTES:
            note_parts.append(MANUAL_NOTES[rid])
        if rid in notes:
            note_parts.append(notes[rid])
        note = " · ".join(note_parts).replace("|", "\\|")
        lines.append(f"| {rid} | {titles[rid]} | {impl} | {tested} | {note} |")

    lines += [
        "",
        f"**Summary:** {c['impl']}/{c['total']} requirements implemented "
        f"({c['locus']} with a named locus, {c['by_test']} asserted only by tests) · "
        f"{c['partial']} partial · {c['external']} external · {c['unclaimed']} unclaimed · "
        f"{c['tested']}/{c['total']} with passing automated coverage · "
        f"**{c['untested']} with no automated test at all** · "
        f"{c['skip']} skipped · {c['fail']} failing.",
        "",
        "## Verification workflow",
        "",
        "1. From the testrig checkout (`pod/` lives next to `repo/`, outside this repository):",
        "   ```",
        "   cd /home/layer/kairos/pod && docker compose run --rm --entrypoint bash dev -lc \\",
        "     'cd /images/repo/vibeOS-local && bash tests/run-tests.sh'",
        "   ```",
        "   boots the kernel on mock engines (no GPU needed), runs every test, writes",
        "   `tests/results.json`, and regenerates this file. A host without `python3-aiohttp`",
        "   skips every `http_*` test and produces a misleading table — use the container.",
        "2. The Claude workflow `.claude/workflows/kairos-verify.js` runs the same command",
        "   through agents and reports/repairs failures (invoke: workflow *kairos-verify*).",
        "3. `unit_requirements_traceability` fails the suite if REQUIREMENTS.md skips an id,",
        "   a test cites an id the document lacks, or this file's denominator drifts.",
        "4. Real-engine e2e (image/video/game on GPU) runs on the image itself:",
        "   `tests/gpu-smoke.sh`, `tests/real-image-smoke.sh` over SSH.",
        "",
        "## Open items beyond automation",
        "",
        "- R31 music: proven on the Radeon 780M (Vulkan set, 2026-09-13, docs/ENGINES-VULKAN-2026-09.md",
        "  section 9) and on the RTX 4070 (CUDA set, 2026-09-14, docs/ENGINES-CUDA-2026-09.md) through",
        "  the kernel with the shipped manifest's `turbo` DiT; no clip was audited by ear.",
        "- R30 iGPU: Vulkan set built for trixie and llama/sd/ace/video/add-ons + the kernel e2e verified",
        "  on the Radeon 780M (same doc). Still open: a boot of the image itself on an AMD-only machine.",
        "- R41/R50 CUDA set: built sm_89+sm_120 and proven on the RTX 4070 (llama, Krea 2, acestep,",
        "  vision, kernel e2e — docs/ENGINES-CUDA-2026-09.md). Still open: video and the image add-ons",
        "  on CUDA, anything on a Blackwell card.",
        "- R64/R65 live in `pod/testrig/` outside this repo — not reproducible from a checkout;",
        "  the suite greps their identity when that checkout sits next to the repo (skips otherwise).",
        "- End-to-end on real engines per tier (image/video/game on the GPU).",
        "- Visual/UX review of the shell and generated apps (human).",
    ]
    return lines, c


def main():
    ran_at, results = load_results()
    # the parallel-LLM tests [R72] run as their own file after the suite (tests/test_llm_parallel.py,
    # tests/run-tests.sh) and write tests/results-llm-parallel.json: the same {name: {reqs, status}}
    # shape, merged in so R72's row is derived from a real run like every other
    results = {**results, **load_results(RESULTS.with_name("results-llm-parallel.json"))[1]}
    # the same for the image-model tests [R76: v7.5 Krea 2 + the Kroma LoRA, Muse since 2026-10-10]
    # (tests/test_image_lora.py -> results-image-lora.json)
    results = {**results, **load_results(RESULTS.with_name("results-image-lora.json"))[1]}
    # and the v7.5 desktop + web tests [R74, R75] (tests/test_desktop_web.py -> results-desktop-web.json)
    results = {**results, **load_results(RESULTS.with_name("results-desktop-web.json"))[1]}
    # and the forge-quality tests [R77] (tests/test_forge_quality.py -> results-forge-quality.json)
    results = {**results, **load_results(RESULTS.with_name("results-forge-quality.json"))[1]}
    # and the WiFi / Bluetooth tests [R78] (tests/test_wifi_bt.py -> results-wifi-bt.json)
    results = {**results, **load_results(RESULTS.with_name("results-wifi-bt.json"))[1]}
    # and the imaging-workflow tests [R79] (tests/test_imaging_workflow.py -> results-imaging-workflow.json)
    results = {**results, **load_results(RESULTS.with_name("results-imaging-workflow.json"))[1]}
    # and the refusal-fallback tests [R86] (tests/test_refusal_fallback.py -> results-refusal-fallback.json)
    results = {**results, **load_results(RESULTS.with_name("results-refusal-fallback.json"))[1]}
    # and the dock / browser tests [R81, R83, R84] (tests/test_dock_browser.py -> results-dock-browser.json)
    results = {**results, **load_results(RESULTS.with_name("results-dock-browser.json"))[1]}
    # and games in windows [R82] (tests/test_games_windows.py -> results-games-windows.json)
    results = {**results, **load_results(RESULTS.with_name("results-games-windows.json"))[1]}
    # and ✨ improve a picture [R85] (tests/test_picture_edit.py -> results-picture-edit.json)
    results = {**results, **load_results(RESULTS.with_name("results-picture-edit.json"))[1]}
    ids, titles, notes = parse_requirements(REQ_DOC.read_text(encoding="utf-8"))
    lines, c = render(ids, titles, notes, results, ran_at)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"docs/PROGRESS.md regenerated: {c['impl']}/{c['total']} implemented, "
          f"{c['tested']} tested, {c['untested']} untested, {c['unclaimed']} unclaimed, "
          f"{c['fail']} failing" + (f"; HOLES {numbering_holes(ids)}" if numbering_holes(ids) else ""))


if __name__ == "__main__":
    main()
