#!/usr/bin/env python3
"""Aggregate bench results and compute the §6.6 score of docs/DESKTOP-TECH-EVAL-2026-09.md.

  python3 bench/score.py [results_dir] [--env vm|host] > tables.md

Per candidate × env: median and spread over runs for every metric, the functional matrix,
the weighted score with every normalisation input shown, and the keep-baseline rules."""
import glob
import json
import os
import statistics
import sys

CANDS = ["baseline", "chromium-wm", "qtile"]
NAMES = {"baseline": "1 baseline", "chromium-wm": "2 chromium+openbox", "qtile": "3a chromium+qtile"}
REIMPL = {"baseline": 0, "chromium-wm": 10, "qtile": 10}          # §4 / §1.1, of 42
SIZE_MIB = {"baseline": 0.0, "chromium-wm": 1.0, "qtile": 7.4}       # §2.1 (+python3-xlib / +qtile)


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.median(xs) if xs else None


def spread(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return (min(xs), max(xs)) if xs else (None, None)


def fmt(v, d=1):
    if v is None:
        return "—"
    return f"{v:.{d}f}"


def fs(xs, d=1):
    """median (min–max) over runs"""
    m = med(xs)
    if m is None:
        return "—"
    lo, hi = spread(xs)
    return f"**{m:.{d}f}** ({lo:.{d}f}–{hi:.{d}f})" if len([x for x in xs if x is not None]) > 1 else f"**{m:.{d}f}**"


def load(results_dir, env):
    runs = {c: [] for c in CANDS}
    for f in sorted(glob.glob(os.path.join(results_dir, f"*-{env}-run*.json"))):
        r = json.load(open(f))
        runs[r["candidate"]].append(r)
    return runs


def warm_opens(r):
    return [o["open_ms"] for o in r["M2"]["opens"] if o["label"] == "warm" and o.get("open_ms") is not None]


def metric_rows(runs):
    """{metric: {cand: [per-run values]}}"""
    M = {}

    def put(k, c, v):
        M.setdefault(k, {}).setdefault(c, []).append(v)
    for c, rs in runs.items():
        for r in rs:
            tag = "cold" if r["M1"].get("cold") else "warm"
            put(f"M1 {tag} ready_ms", c, r["M1"].get("ready_ms"))
            put(f"M1 {tag} dock_painted_ms", c, r["M1"].get("dock_painted_ms"))
            cold = [o["open_ms"] for o in r["M2"]["opens"] if o["label"] == "cold"]
            put("M2 cold_ms", c, cold[0] if cold else None)
            w = warm_opens(r)
            put("M2 warm_p50_ms", c, med(w))
            put("M2 warm_p95_ms", c, sorted(w)[int(0.95 * (len(w) - 1))] if w else None)
            put("M2b map_to_paint_p50_ms", c, med(r.get("M2b", [])))
            put("M2b map_to_paint_max_ms", c, max(r["M2b"]) if r.get("M2b") else None)
            put("M2b white_window_ms_max", c, max(r["M2b_white_bar_ms"]) if r.get("M2b_white_bar_ms") else None)
            put("M2b white_window_ms_p50", c, med(r.get("M2b_white_bar_ms", [])))
            put("M2b white_appbody_ms_p50", c, med(r.get("M2b_white_ms", [])))
            put("M2b white_appbody_ms_max", c, max(r["M2b_white_ms"]) if r.get("M2b_white_ms") else None)
            for n in ("0", "1", "3", "5", "10", "after_close"):
                if n in r["M3"]:
                    put(f"M3 pss_{n}_mib", c, r["M3"][n]["pss_mib"])
                    put(f"M3 rss_{n}_mib", c, r["M3"][n]["rss_mib"])
            if "10" in r["M3"] and "0" in r["M3"]:
                put("M3 pss_per_app_mib", c, (r["M3"]["10"]["pss_mib"] - r["M3"]["0"]["pss_mib"]) / 10)
            put("M3b renderers@10", c, r["M3b"].get("chromium:renderer"))
            put("M3b browsers@10", c, r["M3b"].get("chromium:browser"))
            put("M3b gpu@10", c, r["M3b"].get("chromium:gpu-process"))
            put("M3b procs@10", c, sum(v for k, v in r["M3b"].items() if isinstance(v, int)))
            m5 = r.get("M5", {})
            put("M5 stack_cpu_a1_visible_%core", c, m5.get("a1_visible", {}).get("stack_cpu_pct_one_core"))
            put("M5 stack_cpu_a1_minimized_%core", c, m5.get("a1_minimized", {}).get("stack_cpu_pct_one_core"))
            put("M5 system_idle_a1_visible_%", c, m5.get("a1_visible", {}).get("system_idle_pct"))
            m6 = r.get("M6", {})
            put("M6 ops_per_s", c, m6.get("ops_per_s"))
            put("M6 op_p50_ms", c, (m6.get("all") or {}).get("p50"))
            put("M6 op_p95_ms", c, (m6.get("all") or {}).get("p95"))
            for op in ("move", "resize", "raise"):
                put(f"M6 {op}_p50_ms", c, ((m6.get("per_op") or {}).get(op) or {}).get("p50"))
            for lbl in ("3apps", "10apps"):
                m7 = r.get("M7", {}).get(lbl, {})
                put(f"M7 frames_{lbl}", c, m7.get("frames_median"))
                put(f"M7 stalls_{lbl}", c, m7.get("stalls_median"))
                put(f"M7 interval_p95_{lbl}_ms", c, m7.get("interval_p95_median"))
                jit = r.get("M9", {}).get("M9b", {}).get(lbl, {})
                for proc in ("driver", "qtile"):
                    if proc in jit and jit[proc]:
                        put(f"M9b {proc}_jitter_p99_{lbl}_us", c, jit[proc].get("p99"))
                        put(f"M9b {proc}_jitter_p50_{lbl}_us", c, jit[proc].get("p50"))
            m8 = r.get("M8", {}).get("input_to_photon_ms") or {}
            put("M8 p50_ms", c, m8.get("p50"))
            put("M8 p95_ms", c, m8.get("p95"))
            put("M8 misses", c, r.get("M8", {}).get("misses"))
            m9a = r.get("M9", {}).get("M9a", {})
            put("M9a driver_cpu_ms_per_op", c, m9a.get("driver", {}).get("cpu_ms_per_op"))
            put("M9a qtile_cpu_ms_per_op", c, m9a.get("qtile", {}).get("cpu_ms_per_op"))
            put("M9a shell_js_task_ms_per_op", c, m9a.get("shell_js", {}).get("task_ms_per_op"))
            put("M9c driver_pss_mib", c, (m9a.get("driver", {}).get("pss_kb") or 0) / 1024 or None)
            put("M9c qtile_pss_mib", c, (m9a.get("qtile", {}).get("pss_kb") or 0) / 1024 or None)
    return M


def functional(runs):
    F = {}
    for c, rs in runs.items():
        for r in rs:
            for k, v in r.get("F", {}).items():
                if not k.startswith("F"):
                    continue
                F.setdefault(k, {}).setdefault(c, []).append(v.get("pass") if isinstance(v, dict) else v)
            F.setdefault("F10 (info) browser context menu", {}).setdefault(c, []).append(
                not (r.get("F", {}).get("F10") or {}).get("browser_context_menu"))
    return F


def python_overhead(r, c):
    """M9 per run: (cpu ms/op summed over Python procs, jitter p99 µs (worst proc, 10 apps), Python Pss MiB)"""
    if c == "baseline":      # no Python in the loop at all: 0 by construction (JS cost shown separately)
        return 0.0, 0.0, 0.0
    m9a = r.get("M9", {}).get("M9a", {})
    cpu = sum((m9a.get(p, {}).get("cpu_ms_per_op") or 0) for p in ("driver", "qtile"))
    jit = r.get("M9", {}).get("M9b", {}).get("10apps") or r.get("M9", {}).get("M9b", {}).get("3apps") or {}
    p99 = max([(jit.get(p) or {}).get("p99") or 0 for p in ("driver", "qtile")] or [0])
    pss = sum((m9a.get(p, {}).get("pss_kb") or 0) for p in ("driver", "qtile")) / 1024
    return cpu, p99, pss


F1_FROM = {}      # candidate -> F1 pass taken from another env (VM had one monitor)


def score(runs, env):
    """§6.6: points × clamp((worst − value)/(worst − best)); best = best candidate, worst =
    max(3×best, baseline). Degenerate best == 0: worst = the largest observed value."""
    vals = {}
    for c, rs in runs.items():
        if not rs:
            continue
        v = {}
        v["M3"] = med([r["M3"]["10"]["pss_mib"] for r in rs if "10" in r["M3"]])
        v["M2"] = med([med(warm_opens(r)) for r in rs])
        v["reimpl"] = REIMPL[c]
        v["M7"] = med([r.get("M7", {}).get("10apps", {}).get("stalls_median") for r in rs])
        v["M8"] = med([(r.get("M8", {}).get("input_to_photon_ms") or {}).get("p95") for r in rs])
        po = [python_overhead(r, c) for r in rs]
        v["M9a"] = med([p[0] for p in po])
        v["M9b"] = med([p[1] for p in po])
        v["M9c"] = med([p[2] for p in po])
        v["size"] = SIZE_MIB[c]
        v["M5"] = med([r.get("M5", {}).get("a1_visible", {}).get("stack_cpu_pct_one_core") for r in rs])
        v["F1"] = 1.0 if all(r.get("F", {}).get("F1", {}).get("pass") for r in rs) else 0.0
        if c in F1_FROM:
            v["F1"] = 1.0 if F1_FROM[c] else 0.0
        vals[c] = v
    weights = [("M3", 25, "M3 Pss @10 apps (MiB)"), ("M2", 20, "M2 warm open p50 (ms)"), ("M7", 7.5, "M7 stalls/drag @10 apps"),
               ("M8", 7.5, "M8 input→photon p95 (ms)"), ("M9a", 5, "M9a Python CPU ms/op"), ("M9b", 3, "M9b jitter p99 (µs)"),
               ("M9c", 2, "M9c Python Pss (MiB)"), ("size", 5, "image size Δ (MiB)"), ("M5", 5, "M5 stack CPU % (A1 visible)")]
    rows = []
    total = {c: 0.0 for c in vals}
    detail = {c: {} for c in vals}
    for key, pts, label in weights:
        xs = {c: vals[c][key] for c in vals if vals[c][key] is not None}
        if not xs:
            rows.append((label, pts, "—", "—", {c: None for c in vals}))
            continue
        best = min(xs.values())
        base = xs.get("baseline", best)
        worst = max(3 * best, base) if best > 0 else max(xs.values())
        for c in vals:
            v = xs.get(c)
            if v is None:
                p = 0.0
            elif worst == best:
                p = pts
            else:
                p = pts * max(0.0, min(1.0, (worst - v) / (worst - best)))
            total[c] += p
            detail[c][key] = (v, p)
        rows.append((label, pts, best, worst, {c: detail[c].get(key) for c in vals}))
    for c in vals:
        p = 15 * (1 - vals[c]["reimpl"] / 42)
        total[c] += p
        detail[c]["reimpl"] = (vals[c]["reimpl"], p)
        pf = 5 * vals[c]["F1"]
        total[c] += pf
        detail[c]["F1"] = (vals[c]["F1"], pf)
    return vals, rows, total, detail


def gates(runs):
    out = {}
    for c, rs in runs.items():
        if not rs:
            continue
        g = {}
        F = {k: [r.get("F", {}).get(k, {}) for r in rs] for k in ("F2", "F8", "F9", "F10", "F12")}
        g["F2 pygame visible"] = all(f.get("visible") for f in F["F2"])
        g["F8 vibe.* unchanged"] = all(f.get("pass") for f in F["F8"])
        g["F9 bridge isolation"] = all(f.get("pass") for f in F["F9"])
        g["F10 no Linux chrome"] = all(f.get("pass") for f in F["F10"])
        g["F12 GPU rendering"] = all(f.get("pass") for f in F["F12"]) if any(f.get("pass") is not None for f in F["F12"]) else None
        out[c] = g
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    env = "vm"
    if "--env" in sys.argv:
        env = sys.argv[sys.argv.index("--env") + 1]
    results_dir = args[0] if args else os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    runs = load(results_dir, env)
    present = [c for c in CANDS if runs[c]]
    if "--f1-from" in sys.argv:       # e.g. --f1-from host: the VM's X reported a single monitor
        other = load(results_dir, sys.argv[sys.argv.index("--f1-from") + 1])
        for c in present:
            if other[c]:
                F1_FROM[c] = all(r.get("F", {}).get("F1", {}).get("pass") for r in other[c])
    print(f"### Results — env `{env}` — runs: " + ", ".join(f"{NAMES[c]} ×{len(runs[c])}" for c in present) + "\n")
    M = metric_rows(runs)
    print("| metric | " + " | ".join(NAMES[c] for c in present) + " |")
    print("|---|" + "---|" * len(present))
    for k in M:
        cells = [fs(M[k].get(c, []), 2 if ("ms" in k and "M2" not in k and "M1" not in k) else 1) for c in present]
        print(f"| {k} | " + " | ".join(cells) + " |")
    print("\n#### Functional matrix (PASS count / runs)\n")
    F = functional(runs)
    print("| check | " + " | ".join(NAMES[c] for c in present) + " |")
    print("|---|" + "---|" * len(present))
    for k in sorted(F):
        cells = []
        for c in present:
            v = F[k].get(c, [])
            n = sum(1 for x in v if x is True)
            cells.append(("**PASS**" if n == len(v) and v else "n/a" if all(x is None for x in v) else "FAIL") + f" ({n}/{len(v)})")
        print(f"| {k} | " + " | ".join(cells) + " |")
    print("\n#### Hard gates\n")
    G = gates(runs)
    keys = list(next(iter(G.values())).keys()) if G else []
    print("| gate | " + " | ".join(NAMES[c] for c in present) + " |")
    print("|---|" + "---|" * len(present))
    for k in keys:
        print(f"| {k} | " + " | ".join({True: "pass", False: "**FAIL**", None: "n/a"}[G[c][k]] for c in present) + " |")
    vals, rows, total, detail = score(runs, env)
    print("\n#### Weighted score (§6.6)\n")
    print("| metric (points) | best | worst | " + " | ".join(f"{NAMES[c]} value → pts" for c in present) + " |")
    print("|---|---|---|" + "---|" * len(present))
    for label, pts, best, worst, per in rows:
        cells = []
        for c in present:
            d = per.get(c)
            cells.append("—" if not d else f"{fmt(d[0], 2)} → {d[1]:.1f}")
        print(f"| {label} ({pts}) | {fmt(best, 2) if best != '—' else '—'} | {fmt(worst, 2) if worst != '—' else '—'} | " + " | ".join(cells) + " |")
    print("| reimplementation 15×(1−n/42) (15) | 0 | 42 | " + " | ".join(f"{detail[c]['reimpl'][0]}/42 → {detail[c]['reimpl'][1]:.1f}" for c in present) + " |")
    print("| F1 cross-monitor move (5)" + (" [from host runs]" if F1_FROM else "") + " | pass | fail | " + " | ".join(f"{'pass' if detail[c]['F1'][0] else 'fail'} → {detail[c]['F1'][1]:.1f}" for c in present) + " |")
    print("| **total (100)** | | | " + " | ".join(f"**{total[c]:.1f}**" for c in present) + " |")
    print("\n#### Keep-baseline rules\n")
    base = total.get("baseline")
    winner = max((c for c in present if c != "baseline"), key=lambda c: total[c], default=None)
    if base is not None and winner:
        v = vals
        r1 = total[winner] < base + 10
        r2 = (v[winner]["M3"] or 0) - (v["baseline"]["M3"] or 0) > 400
        flash = med(M.get("M2b white_window_ms_max", {}).get(winner, [])) or 0
        r3 = not all(x for x in G[winner].values() if x is not None) or flash > 50
        r5 = REIMPL[winner] > 25 and not ((v[winner]["M3"] or 1e9) < (v["baseline"]["M3"] or 0) and (v[winner]["M2"] or 1e9) < (v["baseline"]["M2"] or 0))
        r6 = (v[winner]["M8"] or 0) - (v["baseline"]["M8"] or 0) > 30
        for i, (txt, hit) in enumerate([
                (f"no candidate scores ≥ baseline + 10 (baseline {base:.1f}, best {NAMES[winner]} {total[winner]:.1f})", r1),
                (f"winner's M3 @10 exceeds baseline by > 400 MiB ({fmt(v[winner]['M3'], 0)} vs {fmt(v['baseline']['M3'], 0)} MiB)", r2),
                (f"any hard gate fails / M2b white flash > 50 ms (window-level white flash, max per run, median over runs: {fmt(flash, 0)} ms)", r3),
                ("winner changes what generated app source may call (F8 unmodified source: %s)" % ("pass" if G[winner]["F8 vibe.* unchanged"] else "FAIL"), not G[winner]["F8 vibe.* unchanged"]),
                (f"winner needs > 25/42 reimplemented without beating baseline on both M3 and M2 ({REIMPL[winner]}/42)", r5),
                (f"winner's M8 p95 worse than baseline by > 30 ms ({fmt(v[winner]['M8'])} vs {fmt(v['baseline']['M8'])} ms)", r6)], 1):
            print(f"{i}. {'**TRIGGERED**' if hit else 'not triggered'} — {txt}")
    print("\n#### Python overhead inputs (M9)\n")
    for c in present:
        print(f"- {NAMES[c]}: " + ", ".join(f"{k} {fmt(vals[c][k], 3)}" for k in ("M9a", "M9b", "M9c")))


if __name__ == "__main__":
    main()
