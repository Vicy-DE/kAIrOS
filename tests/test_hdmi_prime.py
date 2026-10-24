#!/usr/bin/env python3
"""kAIrOS R91 — a monitor on the HDMI port of the hybrid laptop's NVIDIA GPU joins the one desktop (reverse PRIME,
docs/HDMI-PRIME-2026-10-22.md).

Standalone: `python3 tests/test_hdmi_prime.py` (run-tests.sh runs it after the suite). Writes tests/results-hdmi-prime.json
(not for a KAIROS_TEST_ONLY subset). No X, no GPU, offline:
* the root-side helper iso/config/includes.chroot/usr/local/sbin/kairos-hdmi-prime on a fake sysfs (KAIROS_SYS) with a fake
  modprobe (it "loads" nvidia_drm by creating the DRM card + its connectors), a fake systemctl (a getty restart that does,
  or does not, bring a new X session whose breadcrumb names the NVIDIA-G0 provider) and a fake nvidia-smi: the off switch
  (cmdline, file), the unsupported cases (no NVIDIA, NVIDIA = boot GPU, no NVIDIA X driver / OutputClass), no monitor, the
  module wait, the attach (the X config written, ONE planned restart, state active), the failure paths (revert: the config
  removed, one more planned restart, state failed with the reason, no retry), prestart's clean slate, the udev hook, the
  switch commands;
* the X side: kernel/monitors.py's provider parsing + bind plan on mock-xrandr's provider tables, kairos-desktop.py
  `providers --bind` (the xrandr call by XID, once), plan_layout on the HDMI layouts, .xinitrc's bind-before-layout line;
* the display API (`providers`, `hdmi`), the units / udev rule / build + hook + sync lists, the static X config and the
  module blacklist untouched (the no-HDMI boot is as before), the boot log's hdmi_prime check + planned-restart arithmetic
  (the real kairos-bootlog on a fake ESP), the kernel's settings key, the docs.
"""
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "kernel"))
import monitors as monmath  # noqa: E402

RESULTS = {}
INC = REPO / "iso" / "config" / "includes.chroot"
HELPER = INC / "usr" / "local" / "sbin" / "kairos-hdmi-prime"
XINITRC = INC / "home" / "kairos" / ".xinitrc"
BOOTLOG = INC / "usr" / "local" / "sbin" / "kairos-bootlog"
UNITS = INC / "etc" / "systemd" / "system"
DESKTOP_PY = REPO / "scripts" / "desktop" / "kairos-desktop.py"
MOCK_XRANDR = REPO / "tests" / "mock-xrandr"
# what the new X session's .xinitrc breadcrumb says once the X side bound the NVIDIA GPU screen
PROVLINE_BOUND = ("AMD Radeon 780M Graphics @ pci:0000:05:00.0 bound (source-output, sink-output, sink-offload); "
                  "NVIDIA-G0 bound (sink-output)")        # both sides count the association


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:200]}", flush=True)


class SkipTest(Exception):
    pass


TESTS = []


def test(name, reqs):
    def deco(fn):
        fn._name, fn._reqs = name, reqs
        TESTS.append(fn)
        return fn
    return deco


async def run_one(fn):
    try:
        detail = fn()
        if asyncio.iscoroutine(detail):
            detail = await detail
        record(fn._name, fn._reqs, "pass", detail or "")
        return True
    except SkipTest as e:
        record(fn._name, fn._reqs, "skip", str(e))
    except Exception as e:
        import traceback
        tb = traceback.extract_tb(e.__traceback__)
        where = next((f.lineno for f in reversed(tb) if f.filename == __file__), tb[-1].lineno)
        record(fn._name, fn._reqs, "fail", f"{type(e).__name__}: {str(e)[:600]} @ {where}")
        traceback.print_exc()
    return False


# ------------------------------------------------------------------------------------------ a fake laptop for the helper
class World:
    """sysfs (the GPUs, the nvidia driver binding, the DRM card once "nvidia_drm" is loaded), bin (modprobe, systemctl,
    nvidia-smi), run (the X socket, x.log, the attempt counters, the helper's state + log), etc (the switch, the X config)"""
    AMD, NV = "0x1002", "0x10de"

    def __init__(self, td, gpus=((AMD, "1", "0000:05:00.0"), (NV, "0", "0000:01:00.0")), bound=True, connected=True,
                 x_up=True, restart="ok", cmdline="root=x quiet", switch=None, ddx=True, outputclass=True,
                 provline=PROVLINE_BOUND, wait="2", xwait="3"):
        self.t = Path(td)
        self.sys, self.bin, self.run, self.etc = self.t / "sys", self.t / "bin", self.t / "run", self.t / "etc"
        for d in (self.bin, self.run, self.etc, self.sys / "class" / "drm", self.sys / "bus" / "pci" / "devices"):
            d.mkdir(parents=True, exist_ok=True)
        self.nv = None
        for vendor, boot_vga, slot in gpus:
            d = self.sys / "bus" / "pci" / "devices" / slot
            d.mkdir(parents=True)
            (d / "vendor").write_text(vendor + "\n"); (d / "class").write_text("0x030000\n")
            (d / "boot_vga").write_text(boot_vga + "\n")
            (d / "power").mkdir(); (d / "power" / "runtime_status").write_text("active\n")
            if vendor == self.NV:
                self.nv = slot
        if bound and self.nv:
            self.bind()
        self.calls = self.t / "calls.log"
        card = self.sys / "class" / "drm"
        (self.bin / "modprobe").write_text(
            '#!/bin/sh\necho "modprobe $*" >> "$CALLS"\n'
            'if [ "$1" = nvidia_drm ] && [ -n "$NVSLOT" ]; then\n'
            f'  mkdir -p "{card}/card1" "{card}/card1-HDMI-A-1" "{card}/card1-DP-1"\n'
            f'  ln -sfn "{self.sys}/bus/pci/devices/$NVSLOT" "{card}/card1/device"\n'
            f'  echo "$HDMI" > "{card}/card1-HDMI-A-1/status"; echo disconnected > "{card}/card1-DP-1/status"\nfi\nexit 0\n')
        # a getty restart: the X attempt counter goes up by one; "ok" = the new session leaves its breadcrumbs,
        # "dead" = no new session at all, "loop" = X fails and is retried (the counter jumps by more than one)
        (self.bin / "systemctl").write_text(
            '#!/bin/sh\necho "systemctl $*" >> "$CALLS"\ncase "$*" in *restart*)\n'
            '  n=$(cat "$XCNT" 2>/dev/null || echo 1); echo $((n + 1)) > "$XCNT"\n'
            '  [ "$RESTART" = ok ] && { sleep 0.3; echo "xinitrc: displays: eDP connected" >> "$XLOG"; '
            'echo "xinitrc: providers: $PROVLINE" >> "$XLOG"; }\n'
            '  [ "$RESTART" = loop ] && echo $((n + 2)) > "$XCNT"\n  ;;\nesac\nexit 0\n')
        (self.bin / "nvidia-smi").write_text('#!/bin/sh\necho "P8, 1.00 W, 185 MiB, 8188 MiB"\n')
        for f in self.bin.iterdir():
            f.chmod(0o755)
        (self.run / "x-attempts").write_text("1\n")
        (self.run / "x.log").write_text("xinitrc: displays: eDP connected\n")
        if x_up:
            socket.socket(socket.AF_UNIX).bind(str(self.run / "X0"))
        (self.t / "cmdline").write_text(cmdline + "\n")
        if switch is not None:
            (self.etc / "hdmi").write_text(switch + "\n")
        (self.t / "ddx").write_text("x"); (self.t / "outputclass").write_text("x")
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}", KAIROS_SYS=str(self.sys),
                        CALLS=str(self.calls), NVSLOT=self.nv or "", HDMI="connected" if connected else "disconnected",
                        RESTART=restart, XCNT=str(self.run / "x-attempts"), XLOG=str(self.run / "x.log"), PROVLINE=provline,
                        KAIROS_HDMI_STATE=str(self.run / "hdmi-prime.json"), KAIROS_HDMI_LOG=str(self.run / "hdmi-prime.log"),
                        KAIROS_HDMI_XCONF=str(self.etc / "15-kairos-hdmi-prime.conf"), KAIROS_HDMI_SWITCH=str(self.etc / "hdmi"),
                        KAIROS_HDMI_CMDLINE=str(self.t / "cmdline"), KAIROS_HDMI_XLOG=str(self.run / "x.log"),
                        KAIROS_HDMI_XCNT=str(self.run / "x-attempts"), KAIROS_HDMI_PLANNED=str(self.run / "x-planned-restarts"),
                        KAIROS_HDMI_ATTEMPTS=str(self.run / "hdmi-prime-attempts"),
                        KAIROS_HDMI_DECLINED=str(self.run / "hdmi-prime-declined"),
                        KAIROS_HDMI_DDX=str(self.t / ("ddx" if ddx else "no-ddx")),
                        KAIROS_HDMI_OUTPUTCLASS=str(self.t / ("outputclass" if outputclass else "no-outputclass")),
                        KAIROS_HDMI_XSOCK=str(self.run / "X0"), KAIROS_HDMI_WAIT=wait, KAIROS_HDMI_XWAIT=xwait)

    def bind(self):
        (self.sys / "bus" / "pci" / "drivers" / "nvidia" / self.nv).mkdir(parents=True, exist_ok=True)

    def run_cmd(self, *args):
        r = subprocess.run(["sh", str(HELPER), *args], env=self.env, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, f"{args}: exit {r.returncode} — the helper exits 0 on every path [R63]: {r.stderr}"
        assert not r.stderr, f"{args}: nothing on stderr expected: {r.stderr}"
        return r.stdout

    def state(self):
        p = self.run / "hdmi-prime.json"
        return json.loads(p.read_text()) if p.exists() else None

    def calls_of(self, word):
        return [l for l in (self.calls.read_text().splitlines() if self.calls.exists() else []) if l.startswith(word)]

    def restarts(self):
        return len([l for l in self.calls_of("systemctl") if "restart" in l])

    def counter(self, name):
        p = self.run / name
        return int(p.read_text().strip()) if p.exists() else 0

    def xconf(self):
        return self.etc / "15-kairos-hdmi-prime.conf"

    def log(self):
        p = self.run / "hdmi-prime.log"
        return p.read_text() if p.exists() else ""

    def plug(self, status):
        (self.sys / "class" / "drm" / "card1-HDMI-A-1" / "status").write_text(status + "\n")


# ------------------------------------------------------------------------------------------------------- the helper
@test("hp_helper_and_units_contracts", ["R91", "R41", "R50", "R63"])
def t_contracts():
    """the helper parses and is executable, every path it touches is overridable (the suite's fake laptop), the display
    modules are loaded EXPLICITLY with modeset=1 and no fbdev (the blacklist only stops alias autoloading), the attach
    session's X config switches AutoAddGPU on through a LATER file (a later ServerFlags file overrides the earlier one —
    measured on xserver 21.1); the boot unit runs its ExecStartPre before getty@tty1 and is never ordered after
    multi-user.target (D7), is simple (getty never waits for the module wait), is off with kairos.hdmi=0, and both units
    ignore the script's exit; the udev rule fires on the NVIDIA DRM card's change events only and never blocks the worker;
    the build, the hook and the rig sync install + enable it; and the no-HDMI boot is as before: the static X config still
    says AutoAddGPU off, the module blacklist is unchanged, kairos-gpu-setup is untouched by this round"""
    src = HELPER.read_text()
    subprocess.run(["sh", "-n", str(HELPER)], check=True)
    assert os.access(HELPER, os.X_OK), "kairos-hdmi-prime lost its exec bit"
    for var in ("KAIROS_SYS", "KAIROS_HDMI_STATE", "KAIROS_HDMI_LOG", "KAIROS_HDMI_XCONF", "KAIROS_HDMI_SWITCH",
                "KAIROS_HDMI_CMDLINE", "KAIROS_HDMI_XLOG", "KAIROS_HDMI_XCNT", "KAIROS_HDMI_PLANNED", "KAIROS_HDMI_DDX",
                "KAIROS_HDMI_ATTEMPTS", "KAIROS_HDMI_DECLINED",
                "KAIROS_HDMI_OUTPUTCLASS", "KAIROS_HDMI_XSOCK", "KAIROS_HDMI_WAIT", "KAIROS_HDMI_XWAIT"):
        assert f"${{{var}:-" in src, f"{var} not overridable"
    assert "modprobe nvidia_modeset 2>/dev/null" in src and "modprobe nvidia_drm modeset=1 fbdev=0 2>/dev/null" in src
    assert "'    Option \"AutoAddGPU\" \"on\"'" in src and "15-kairos-hdmi-prime.conf" in src
    assert src.rstrip().endswith("exit 0"), "exit 0 on every path"
    assert not re.search(r"^\s*: >", src, re.M), "no `: > file` (a failed redirection on a special builtin ends dash with exit 2)"
    for cmd in ("prestart", "boot", "react", "event", "probe", "attach", "attach-now", "later", "status", "on", "off"):
        assert re.search(rf"^  [a-z|-]*(?<![a-z-]){cmd}(?![a-z-])[a-z|-]*\)", src, re.M), cmd   # a case label, possibly shared (react|attach)
    unit = (UNITS / "kairos-hdmi-prime.service").read_text()
    d = [l for l in unit.splitlines() if l and not l.startswith("#")]
    assert "Before=getty@tty1.service" in d and "After=basic.target" in d and "Type=simple" in d, d
    assert not any("nvidia-load" in l for l in d), "not ordered after nvidia-load: the script waits for the driver itself (the D7 detector self-check)"
    assert not any(l.startswith("After=") and "multi-user.target" in l for l in d), "D7: never after multi-user.target"
    assert "ExecStartPre=-/usr/local/sbin/kairos-hdmi-prime prestart" in d and "ExecStart=-/usr/local/sbin/kairos-hdmi-prime boot" in d
    assert not any(l.startswith("Condition") for l in d), "no unit condition: prestart must run on every boot (a stale attach file)"
    assert "WantedBy=multi-user.target" in d
    ev = [l for l in (UNITS / "kairos-hdmi-prime-event.service").read_text().splitlines() if l and not l.startswith("#")]
    assert "Type=oneshot" in ev and "ExecStart=-/usr/local/sbin/kairos-hdmi-prime react" in ev and not any(l.startswith("[Install]") for l in ev)
    rule = [l for l in (INC / "etc" / "udev" / "rules.d" / "80-kairos-hdmi-prime.rules").read_text().splitlines() if l and not l.startswith("#")]
    assert len(rule) == 1 and 'ACTION=="change"' in rule[0] and 'SUBSYSTEM=="drm"' in rule[0] and 'ATTRS{vendor}=="0x10de"' in rule[0] \
        and 'RUN+="/usr/local/sbin/kairos-hdmi-prime event"' in rule[0], rule
    assert 'systemctl start --no-block "$EVENT_UNIT"' in src, "the udev hook only starts the event unit"
    b = (REPO / "iso" / "build-img.sh").read_text()
    for f in ("usr/local/sbin/kairos-hdmi-prime", "etc/systemd/system/kairos-hdmi-prime.service",
              "etc/systemd/system/kairos-hdmi-prime-event.service", "etc/udev/rules.d/80-kairos-hdmi-prime.rules"):
        assert f'"$INC/{f}"' in b, f"build-img.sh does not install {f}"
    assert "kairos-prewarm.service kairos-hdmi-prime.service" in b and "multi-user.target.wants/kairos-hdmi-prime.service" in b
    hook = (REPO / "iso" / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert "/usr/local/sbin/kairos-hdmi-prime" in hook and "kairos-hdmi-prime.service" in hook
    sync = (REPO / "dev" / "rig" / "testrig" / "sync-guest.sh").read_text()
    assert "/usr/local/sbin/kairos-hdmi-prime" in sync and "multi-user.target.wants/kairos-hdmi-prime.service" in sync
    xc = (INC / "etc" / "X11" / "xorg.conf.d" / "10-kairos-single-gpu.conf").read_text()
    assert re.search(r'Section\s+"ServerFlags"\s+Option\s+"AutoAddGPU"\s+"off"\s+EndSection', xc), "the default X start is unchanged"
    bl = [l.strip() for l in (INC / "etc" / "modprobe.d" / "kairos-nvidia-hybrid.conf").read_text().splitlines()
          if l.strip() and not l.startswith("#")]
    assert bl == ["blacklist nvidia_drm", "blacklist nvidia_modeset"], bl
    gs = (INC / "usr" / "local" / "sbin" / "kairos-gpu-setup").read_text()
    assert "hdmi" not in gs.lower(), "kairos-gpu-setup is not part of this feature (the default boot path stays byte-identical)"
    return "helper + units + udev rule + build/hook/sync lists wired; modeset=1 fbdev=0, AutoAddGPU on only in the attach file; default X config + blacklist unchanged"


@test("hp_switch_off", ["R91"])
def t_switch():
    """the off switch needs no rebuild: kernel cmdline kairos.hdmi=0, or /etc/kairos/hdmi = off (what the settings key
    writes); kairos.hdmi=1 on the cmdline beats the file. Off = nothing is loaded, nothing restarted, state `off` with
    the reason; `off` and `on` write the file (on also asks the event unit to probe now, and that probe asks the user)"""
    with tempfile.TemporaryDirectory() as td:
        w = World(td, cmdline="root=x quiet kairos.hdmi=0")
        w.run_cmd("boot")
        st = w.state()
        assert st["state"] == "off" and "kairos.hdmi=0" in st["reason"], st
        assert not w.calls_of("modprobe") and not w.restarts(), w.calls.read_text() if w.calls.exists() else ""
    with tempfile.TemporaryDirectory() as td:
        w = World(td, switch="off")
        w.run_cmd("boot")
        st = w.state()
        assert st["state"] == "off" and "says off" in st["reason"] and not w.calls_of("modprobe"), st
    with tempfile.TemporaryDirectory() as td:
        w = World(td, switch="off", cmdline="quiet kairos.hdmi=1")
        w.run_cmd("boot")
        assert w.state()["state"] == "active", w.state()
    with tempfile.TemporaryDirectory() as td:
        w = World(td)
        w.run_cmd("off")
        assert (w.etc / "hdmi").read_text().strip() == "off" and w.state()["state"] == "off", w.state()
        w.run_cmd("on")
        assert (w.etc / "hdmi").read_text().strip() == "on"
        assert "systemctl start --no-block kairos-hdmi-prime-event.service" in w.calls.read_text()
        w.run_cmd("react")                               # what the event unit then runs: the switch is re-read, the user asked
        assert w.state()["state"] == "consent" and not w.restarts(), w.state()
    return "kairos.hdmi=0 and the file -> off, nothing loaded; kairos.hdmi=1 beats the file; on/off write the file, on probes via the event unit"


@test("hp_unsupported_machines", ["R91", "R41"])
def t_unsupported():
    """no NVIDIA GPU, an NVIDIA GPU that IS the boot GPU (X runs on it: its HDMI is a plain output, R41/R50), no NVIDIA
    X driver, no OutputClass for nvidia-drm (X would hand the GPU to modesetting — the 92-s stall): state `unsupported`
    with the reason, the display modules never loaded, nothing restarted"""
    cases = [("no-nvidia", dict(gpus=((World.AMD, "1", "0000:05:00.0"),)), "no NVIDIA GPU"),
             ("nvidia-boot", dict(gpus=((World.NV, "1", "0000:01:00.0"),)), "boot GPU"),
             ("no-ddx", dict(ddx=False), "no NVIDIA X driver"),
             ("no-outputclass", dict(outputclass=False), "modesetting")]
    for name, kw, phrase in cases:
        with tempfile.TemporaryDirectory() as td:
            w = World(td, **kw)
            w.run_cmd("boot")
            st = w.state()
            assert st["state"] == "unsupported" and phrase in st["reason"], (name, st)
            assert not w.calls_of("modprobe") and not w.restarts(), name
    return "no NVIDIA / NVIDIA = boot GPU / no nvidia_drv.so / no OutputClass -> unsupported, no modprobe, no restart"


@test("hp_no_monitor_then_plug", ["R91", "R90"])
def t_no_monitor():
    """no monitor on the NVIDIA: the display modules are loaded (modeset=1 fbdev=0 — the running X ignores the new DRM
    device: AutoAddGPU off), the connectors read, state `absent`, no X config, no restart, and the 4070's power noted;
    a monitor plugged later = a DRM change event -> `react` -> the attach"""
    with tempfile.TemporaryDirectory() as td:
        w = World(td, connected=False)
        w.run_cmd("boot")
        st = w.state()
        assert st["state"] == "absent" and "no monitor" in st["reason"] and "HDMI-A-1=disconnected" in st["connectors"], st
        assert w.calls_of("modprobe") == ["modprobe nvidia_modeset", "modprobe nvidia_drm modeset=1 fbdev=0"], w.calls_of("modprobe")
        assert not w.restarts() and not w.xconf().exists() and w.counter("x-planned-restarts") == 0
        w.plug("connected")
        w.run_cmd("react")                               # the desktop is up: ASK, never restart on its own
        st = w.state()
        assert st["state"] == "consent" and "waiting for the user" in st["reason"] and not w.restarts() and not w.xconf().exists(), st
        w.run_cmd("attach-now")                          # "Restart now"
        st = w.state()
        assert st["state"] == "active" and w.restarts() == 1 and w.counter("x-planned-restarts") == 1, (st, w.calls.read_text())
        assert w.calls_of("modprobe") == ["modprobe nvidia_modeset", "modprobe nvidia_drm modeset=1 fbdev=0"], "loaded once"
        assert "power (before attach)" in w.log() and "power (after attach)" in w.log() and "P8, 1.00 W, 185 MiB" in w.log()
    return "absent: modules loaded once (modeset=1 fbdev=0), no restart; plug -> react -> consent (no restart) -> attach-now -> active with one planned restart; power noted"


@test("hp_waits_for_the_nvidia_module", ["R91", "R50"])
def t_wait():
    """kairos-gpu-setup loads the nvidia module in the BACKGROUND on the hybrid laptop (49 s off a USB stick, R50): the
    boot step waits for the driver binding (bounded) instead of failing; unbound at the end of the wait = `absent` with
    that reason, and a later `react` (the module came, a monitor is there) attaches"""
    with tempfile.TemporaryDirectory() as td:
        w = World(td, bound=False, wait="2")
        t0 = time.monotonic()
        w.run_cmd("boot")
        took = time.monotonic() - t0
        st = w.state()
        assert st["state"] == "absent" and "not bound" in st["reason"] and took >= 1.8, (st, took)
        assert not w.calls_of("modprobe"), "never modprobe the display modules before the driver is bound"
        assert "waited 2s for the nvidia module" in w.log(), w.log()
        w.bind()
        w.run_cmd("react")
        assert w.state()["state"] == "consent" and not w.restarts(), w.state()
        w.run_cmd("attach-now")
        assert w.state()["state"] == "active" and w.restarts() == 1, w.state()
    return f"unbound: waited {took:.1f} s, absent + reason, no modprobe; bound later + react -> consent -> attach-now -> active"


@test("hp_attach_one_planned_restart", ["R91", "R90", "R51"])
def t_attach():
    """the attach: the X config for THIS session (AutoAddGPU on), the planned-restart counter the boot log subtracts,
    exactly ONE `systemctl restart getty@tty1.service`, then the new session's breadcrumb "NVIDIA-G0 bound" -> state
    `active` (the providers line kept, the attempts counted); a second event in the attached session restarts nothing
    (plug/unplug there is R90's hot-plug path); `status` prints the state"""
    with tempfile.TemporaryDirectory() as td:
        w = World(td)
        w.run_cmd("boot")
        st = w.state()
        assert st["state"] == "active" and "NVIDIA-G0 bound" in st["providers"] and st["attempts"] == 1, st
        assert "HDMI-A-1=connected" in st["connectors"] and st["gpu"] == "0000:01:00.0", st
        conf = w.xconf().read_text()
        assert 'Section "ServerFlags"' in conf and 'Option "AutoAddGPU" "on"' in conf and "EndSection" in conf, conf
        assert w.restarts() == 1 and "systemctl restart getty@tty1.service" in w.calls.read_text()
        assert w.counter("x-planned-restarts") == 1 and w.counter("x-attempts") == 2
        w.run_cmd("react")
        w.run_cmd("attach")
        assert w.restarts() == 1 and w.state()["state"] == "active", "no second restart in the attached session"
        assert json.loads(w.run_cmd("status"))["state"] == "active"
        assert "state pending" in w.log() and "state active" in w.log()
    return "config (AutoAddGPU on) + 1 planned restart + the breadcrumb -> active; react/attach afterwards restart nothing"


@test("hp_attach_failure_reverts", ["R91", "R63"])
def t_failure():
    """every failure of the attach leaves the desktop running WITHOUT HDMI and says why: no new session at all, X looping
    (the attempt counter jumps), a session without the NVIDIA-G0 provider, a provider that could not be bound — the config
    is removed, ONE more planned restart brings the plain session back, state `failed` with the reason, and no further
    attach this boot (react ignores it)"""
    cases = [("dead", dict(restart="dead"), "no providers breadcrumb"),
             ("loop", dict(restart="loop"), "did not come up"),
             ("no-g0", dict(provline="AMD Radeon 780M Graphics @ pci:0000:05:00.0 unbound (source-output, sink-output)"), "no NVIDIA-G0 provider"),
             ("unbound", dict(provline="AMD Radeon 780M Graphics @ pci:0000:05:00.0 unbound (source-output); NVIDIA-G0 unbound (no caps)"), "could not be bound")]
    for name, kw, phrase in cases:
        with tempfile.TemporaryDirectory() as td:
            w = World(td, **kw)
            w.run_cmd("boot")
            st = w.state()
            assert st["state"] == "failed" and phrase in st["reason"], (name, st)
            assert not w.xconf().exists(), (name, "the attach config must be gone")
            assert w.restarts() == 2 and w.counter("x-planned-restarts") == 2, (name, w.calls.read_text())
            assert "reverting" in w.log() and "power (after revert)" in w.log(), (name, w.log())
            w.run_cmd("react")
            assert w.restarts() == 2 and w.state()["state"] == "failed", (name, "no retry this boot")
    with tempfile.TemporaryDirectory() as td:          # no X session at all: nothing to attach to, nothing restarted
        w = World(td, x_up=False)
        w.run_cmd("boot")
        assert w.state()["state"] == "failed" and "no X session" in w.state()["reason"] and not w.restarts()
    return "dead / looping X / no NVIDIA-G0 / unbound -> failed + reason, config removed, 1 revert restart, no retry; no X -> failed, no restart"


@test("hp_prestart_clean_x_start", ["R91", "R41"])
def t_prestart():
    """ExecStartPre before getty@tty1: whatever the last session left (the attach config, the state, the counters) is
    gone before X starts — every boot starts X with AutoAddGPU off, exactly as before R91"""
    with tempfile.TemporaryDirectory() as td:
        w = World(td)
        w.run_cmd("boot")
        assert w.xconf().exists() and w.state()["state"] == "active" and w.counter("x-planned-restarts") == 1
        w.run_cmd("prestart")
        assert not w.xconf().exists() and w.state() is None, "config + state gone"
        assert w.counter("x-planned-restarts") == 0 and w.counter("hdmi-prime-attempts") == 0
        assert "prestart: clean X start" in w.log()
        assert json.loads(w.run_cmd("status"))["state"] == "n/a"
    return "prestart removes the attach config, the state and the counters; status says n/a"


@test("hp_udev_event_hook", ["R91"])
def t_event():
    """the udev RUN hook does nothing but start the event unit without blocking (the udev worker must never wait for a
    probe or an X restart); the event unit's `react` is what probes"""
    with tempfile.TemporaryDirectory() as td:
        w = World(td)
        w.run_cmd("event")
        assert w.calls_of("systemctl") == ["systemctl start --no-block kairos-hdmi-prime-event.service"], w.calls.read_text()
        assert not w.calls_of("modprobe") and w.state() is None
    return "event -> systemctl start --no-block kairos-hdmi-prime-event.service, nothing else"


@test("hp_hotplug_needs_consent", ["R91", "R90"])
def t_consent():
    """a monitor plugged while the desktop is up NEVER restarts X on its own: `react` ends in state `consent` (the
    kernel's /api/status carries it, the shell shows "HDMI monitor detected — restart the desktop to use it (open
    windows will close)" with Restart now / Not now); a second event keeps that state (no new toast); "Not now"
    (`later`) -> `declined`, remembered for this boot: further events stay quiet; "Restart now" (`attach-now`, also a
    display-settings entry) attaches with the max-1-per-boot guard; prestart forgets the answer; the monitor gone
    again before the answer -> attach-now does nothing"""
    with tempfile.TemporaryDirectory() as td:
        w = World(td, connected=False)
        w.run_cmd("boot")
        assert w.state()["state"] == "absent"
        w.plug("connected")
        w.run_cmd("react")
        st = w.state()
        assert st["state"] == "consent" and "Restart now / Not now" in st["reason"], st
        assert not w.restarts() and not w.xconf().exists() and w.counter("x-planned-restarts") == 0
        first = st["updated"]
        w.run_cmd("react")
        assert w.state()["state"] == "consent" and not w.restarts(), "a second event: still waiting, no restart"
        assert w.state()["updated"] == first, "the state keeps its stamp: the shell asks once per stamp, no second toast"
        w.plug("disconnected"); w.run_cmd("react")
        assert w.state()["state"] == "absent" and "left" in w.state()["reason"], "the monitor gone while asking -> absent"
        w.plug("connected"); w.run_cmd("react")
        assert w.state()["state"] == "consent" and w.state()["updated"] >= first, "back again -> asked again"
        w.run_cmd("later")
        st = w.state()
        assert st["state"] == "declined" and "Not now" in st["reason"] and (w.run / "hdmi-prime-declined").exists(), st
        w.run_cmd("react")
        assert w.state()["state"] == "declined" and not w.restarts(), "remembered for the session: no toast state, no restart"
        w.run_cmd("attach-now")                            # the display-settings entry / the button again
        st = w.state()
        assert st["state"] == "active" and w.restarts() == 1 and w.counter("x-planned-restarts") == 1, (st, w.calls.read_text())
        assert not (w.run / "hdmi-prime-declined").exists()
        w.run_cmd("attach-now")
        assert w.restarts() == 1, "already attached: nothing"
        w.run_cmd("prestart")
        assert not (w.run / "hdmi-prime-declined").exists() and w.state() is None
    with tempfile.TemporaryDirectory() as td:            # consent asked, then the monitor pulled before the answer
        w = World(td, connected=False)
        w.run_cmd("boot"); w.plug("connected"); w.run_cmd("react")
        assert w.state()["state"] == "consent"
        w.plug("disconnected")
        w.run_cmd("attach-now")
        assert not w.restarts() and w.state()["state"] == "absent" and "attach-now: no monitor" in w.log(), w.log()
    with tempfile.TemporaryDirectory() as td:            # the boot path stays automatic (no desktop windows yet)
        w = World(td)
        w.run_cmd("boot")
        assert w.state()["state"] == "active" and w.restarts() == 1
    return "react -> consent (no restart, kept on a 2nd event); later -> declined, remembered; attach-now -> active once; prestart forgets; monitor gone -> nothing; boot path automatic"


# ---------------------------------------------------------------------------------------------------------- the X side
def xrandr_text(layout=None, providers=None):
    env = dict(os.environ, MOCK_XRANDR_QUERY="", MOCK_XRANDR_LAYOUT=layout or "2", MOCK_XRANDR_PROVIDERS=providers or "amd")
    env.pop("MOCK_XRANDR_LOG", None)
    return subprocess.run([str(MOCK_XRANDR), "--listproviders" if providers else "--query"], capture_output=True, text=True,
                          env=env).stdout


def desktop_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("kdesk", DESKTOP_PY)
    kd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kd)
    return kd


@test("hp_providers_parse_and_bind_plan", ["R91", "R90"])
def t_providers():
    """kernel/monitors.py: `xrandr --listproviders` parsed (ids, caps, associations, names with spaces), the bind plan
    (every unbound output sink that is not the main provider, by XID), the breadcrumb summary; the HDMI output on the
    NVIDIA GPU screen (HDMI-1-0) goes through plan_layout like any other output"""
    amd = monmath.parse_providers(xrandr_text(providers="amd"))
    assert len(amd) == 1 and amd[0]["name"] == "AMD Radeon 780M Graphics @ pci:0000:05:00.0" and amd[0]["id"] == "0x55" \
        and amd[0]["caps"] == ["Source Output", "Sink Output", "Sink Offload"] and amd[0]["associated"] == 0, amd
    assert monmath.provider_bind_plan(amd) == []
    un = monmath.parse_providers(xrandr_text(providers="nvidia-unbound"))
    assert [p["name"] for p in un] == ["AMD Radeon 780M Graphics @ pci:0000:05:00.0", "NVIDIA-G0"] and un[1]["caps"] == ["Sink Output"]
    assert monmath.provider_bind_plan(un) == [("NVIDIA-G0", "AMD Radeon 780M Graphics @ pci:0000:05:00.0", "0x1f8", "0x55")]
    assert monmath.providers_summary(un) == ("AMD Radeon 780M Graphics @ pci:0000:05:00.0 unbound (source-output, sink-output, sink-offload); "
                                             "NVIDIA-G0 unbound (sink-output)"), monmath.providers_summary(un)
    bd = monmath.parse_providers(xrandr_text(providers="nvidia-bound"))
    assert bd[1]["associated"] == 1 and monmath.provider_bind_plan(bd) == [] and "NVIDIA-G0 bound (sink-output)" in monmath.providers_summary(bd)
    nc = monmath.parse_providers(xrandr_text(providers="nvidia-nocap"))
    assert nc[1]["caps"] == [] and monmath.provider_bind_plan(nc) == [] and "NVIDIA-G0 unbound (no caps)" in monmath.providers_summary(nc)
    assert monmath.parse_providers("") == [] and monmath.providers_summary([]) == "none"
    real = ("Providers: number : 2\nProvider 0: id: 0x221 cap: 0x9, Source Output, Sink Offload crtcs: 3 outputs: 6 associated providers: 0 name:modesetting\n"
            "Provider 1: id: 0x1f8 cap: 0x0 crtcs: 0 outputs: 0 associated providers: 0 name:NVIDIA-G0\n")   # the README's example
    r = monmath.parse_providers(real)
    assert r[0]["caps"] == ["Source Output", "Sink Offload"] and r[1]["caps"] == [] and monmath.provider_bind_plan(r) == []
    kd = desktop_module()
    outs = monmath.parse_xrandr(xrandr_text(layout="hdmi-plugged"))
    a, why = kd.plan_layout(outs)
    assert why == "extend" and a == ["--output", "eDP", "--mode", "1920x1080", "--pos", "0x0", "--primary",
                                     "--output", "HDMI-1-0", "--auto", "--right-of", "eDP"], a
    mons = monmath.from_outputs(monmath.parse_xrandr(xrandr_text(layout="hdmi")))
    assert [m["id"] for m in mons] == ["eDP", "HDMI-1-0"] and monmath.union(mons) == {"x": 0, "y": 0, "w": 3840, "h": 1080}
    return "providers parsed (4 tables + the README's), bind plan by XID only for an unbound sink, summary; HDMI-1-0 laid out right of the panel"


@test("hp_x_side_binds_once", ["R91", "R90"])
def t_bind():
    """kairos-desktop.py `providers --bind` on mock-xrandr: an unbound NVIDIA-G0 -> ONE `--setprovideroutputsource 0x1f8
    0x55`; bound / the AMD alone / a provider without the sink capability -> no call; the line printed is the breadcrumb"""
    res = {}
    for prov in ("nvidia-unbound", "nvidia-bound", "amd", "nvidia-nocap"):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td); (t / "bin").mkdir(); (t / "bin" / "xrandr").symlink_to(MOCK_XRANDR)
            env = dict(os.environ, PATH=f"{t / 'bin'}:{os.environ.get('PATH', '')}", MOCK_XRANDR_PROVIDERS=prov,
                       MOCK_XRANDR_QUERY="", MOCK_XRANDR_LOG=str(t / "xrandr.log"))
            r = subprocess.run([sys.executable, str(DESKTOP_PY), "providers", "--bind"], env=env, capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, r.stderr
            sets = [l for l in (t / "xrandr.log").read_text().splitlines() if l.startswith("--setprovideroutputsource")]
            res[prov] = (sets, r.stdout.strip(), r.stderr)
    assert res["nvidia-unbound"][0] == ["--setprovideroutputsource\t0x1f8\t0x55"], res["nvidia-unbound"]
    assert "provider NVIDIA-G0 -> output source of AMD Radeon 780M Graphics @ pci:0000:05:00.0" in res["nvidia-unbound"][2], "the bind log goes to stderr: stdout is the breadcrumb"
    assert res["nvidia-unbound"][1] == "AMD Radeon 780M Graphics @ pci:0000:05:00.0 unbound (source-output, sink-output, sink-offload); NVIDIA-G0 unbound (sink-output)"
    for prov in ("nvidia-bound", "amd", "nvidia-nocap"):
        assert res[prov][0] == [], (prov, res[prov])
    assert res["nvidia-bound"][1] == PROVLINE_BOUND, res["nvidia-bound"][1]
    assert res["amd"][1] == "AMD Radeon 780M Graphics @ pci:0000:05:00.0 unbound (source-output, sink-output, sink-offload)"
    src = DESKTOP_PY.read_text()
    w = src[src.index("    def watch(self):"):src.index("    @staticmethod")]
    assert w.count("bind_providers()") == 2 and w.index("bind_providers()") < w.index("self.hotplug()                                   # the connected set at start")
    assert w.index("bind_providers()                         # [R91]") < w.index("                self.hotplug()\n            pending_rr = pending_win = False")
    return "unbound -> one --setprovideroutputsource 0x1f8 0x55; bound/amd/no-cap -> none; the watcher binds at start and on RandR events"


@test("hp_xinitrc_binds_before_layout", ["R91", "R90", "R51"])
def t_xinitrc():
    """.xinitrc binds the providers BEFORE the layout step (the HDMI outputs are in the first layout), leaves the
    `xinitrc: providers:` breadcrumb kairos-hdmi-prime judges the attach by, falls back to xrandr --listproviders
    without the X side script, and defines DESK before using it; the line runs against mock-xrandr"""
    xi = XINITRC.read_text()
    subprocess.run(["sh", "-n", str(XINITRC)], check=True)
    code = "\n".join(l for l in xi.splitlines() if not l.lstrip().startswith("#"))
    line = next(l for l in code.splitlines() if l.startswith('echo "xinitrc: providers:'))
    assert 'python3 "$DESK" providers --bind' in line and "xrandr --listproviders" in line, line
    assert code.index("DESK=/opt/vibeos/os/scripts/desktop/kairos-desktop.py") < code.index(line) < code.index("layout_extend() {") \
        < code.index("/usr/local/sbin/kairos-display-apply || layout_extend") < code.index('echo "xinitrc: displays:')
    assert code.count("DESK=/opt/vibeos/os/scripts/desktop/kairos-desktop.py") == 1
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "bin").mkdir(); (t / "bin" / "xrandr").symlink_to(MOCK_XRANDR)
        env = dict(os.environ, PATH=f"{t / 'bin'}:{os.environ.get('PATH', '')}", MOCK_XRANDR_PROVIDERS="nvidia-unbound",
                   MOCK_XRANDR_QUERY="", MOCK_XRANDR_LOG=str(t / "xrandr.log"))
        r = subprocess.run(["sh", "-c", f"DESK={DESKTOP_PY}\n{line}\n"], env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and r.stdout.startswith("xinitrc: providers: AMD Radeon 780M Graphics") and "NVIDIA-G0" in r.stdout, r
        assert "--setprovideroutputsource\t0x1f8\t0x55" in (t / "xrandr.log").read_text()
        r = subprocess.run(["sh", "-c", f"DESK=/nonexistent/kairos-desktop.py\n{line}\n"], env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and r.stdout.startswith("xinitrc: providers: Providers: number : 2;"), r.stdout
    return "bind + breadcrumb before the layout, DESK defined first, the awk-free fallback prints xrandr's own list"


# ---------------------------------------------------------------------------------------- the kernel: display API, settings
@test("hp_display_api_providers_and_hdmi", ["R91", "R53", "R90"])
async def t_display_api():
    """vibe.hw.display.list carries `providers` (the RandR 1.4 providers) and `hdmi` (kairos-hdmi-prime's state; n/a
    where it never ran); hw.hdmi_switch runs the helper's on|off (silently absent on a dev box)"""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "bin").mkdir(); (t / "bin" / "xrandr").symlink_to(MOCK_XRANDR)
        state = t / "hdmi-prime.json"
        state.write_text(json.dumps({"state": "active", "reason": "NVIDIA-G0 is an output source of the desktop", "gpu": "0000:01:00.0",
                                     "connectors": "HDMI-A-1=connected", "providers": PROVLINE_BOUND, "attempts": 1}))
        fake = t / "kairos-hdmi-prime"
        fake.write_text('#!/bin/sh\necho "$1" >> "$0.log"\necho ok\n'); fake.chmod(0o755)
        os.environ.update(PATH=f"{t / 'bin'}:{os.environ.get('PATH', '')}", MOCK_XRANDR_LAYOUT="hdmi", MOCK_XRANDR_QUERY="",
                          MOCK_XRANDR_PROVIDERS="nvidia-bound", MOCK_XRANDR_LOG=str(t / "xrandr.log"),
                          KAIROS_HDMI_STATE=str(state), KAIROS_HDMI_PRIME=str(fake), VIBEOS_DIR=str(t / "vibeos"))
        for m in ("hw", "monitors"):
            sys.modules.pop(m, None)
        import importlib
        hw = importlib.import_module("hw")
        d = await hw.display_list()
        assert d["available"] and [m["id"] for m in d["monitors"]] == ["eDP", "HDMI-1-0"] and d["desktop"]["w"] == 3840, d
        assert [p["name"] for p in d["providers"]] == ["AMD Radeon 780M Graphics @ pci:0000:05:00.0", "NVIDIA-G0"] and d["providers"][1]["associated"] == 1
        assert d["hdmi"]["state"] == "active" and "HDMI-A-1=connected" in d["hdmi"]["connectors"], d["hdmi"]
        state.unlink()
        d = await hw.display_list()
        assert d["hdmi"]["state"] == "n/a" and "has not run" in d["hdmi"]["reason"], d["hdmi"]
        r = await hw.hdmi_switch("off"); r2 = await hw.hdmi_switch("on")
        assert r["ok"] and r2["ok"] and (t / "kairos-hdmi-prime.log").read_text().split() == ["off", "on"]
        a = await hw.handle({"device": "display", "op": "hdmi", "action": "restart"})     # "Restart now" from the toast
        b = await hw.handle({"device": "display", "op": "hdmi", "action": "later"})       # "Not now"
        assert a["ok"] and b["ok"] and (t / "kairos-hdmi-prime.log").read_text().split() == ["off", "on", "attach-now", "later"], (a, b)
        assert (await hw.handle({"device": "display", "op": "hdmi", "action": "maybe"}))["ok"] is False
        os.environ["KAIROS_HDMI_PRIME"] = str(t / "missing")
        importlib.reload(hw)
        assert (await hw.hdmi_switch("off"))["ok"] is False
    return "display.list: providers (bound NVIDIA-G0) + hdmi state (active / n/a); hdmi_switch on|off and the hdmi op restart|later reach the helper, absent -> ok false"


@test("hp_settings_key_and_docs", ["R91", "R59"])
def t_settings_docs():
    """the kernel knows the `hdmi` setting (default on, on|off, the change handed to hw.hdmi_switch — the live round trip
    is unit_hdmi_setting_key in test_kernel.py); the docs the coder LLM reads and the requirements carry R91"""
    src = (REPO / "kernel" / "server.py").read_text()
    assert '"hdmi": "on"}' in src and 'HDMI_MODES = ("on", "off")' in src and 'elif key == "hdmi":' in src \
        and 'asyncio.create_task(hw.hdmi_switch(changes["hdmi"]))' in src
    disp = (REPO / "docs" / "api" / "display.md").read_text()
    assert "hdmi" in disp and "providers" in disp and "NVIDIA" in disp, "docs/api/display.md must describe hdmi + providers"
    sett = (REPO / "docs" / "api" / "system-settings.md").read_text()
    assert "`hdmi`" in sett, "docs/api/system-settings.md must list the hdmi key"
    req = (REPO / "docs" / "REQUIREMENTS.md").read_text()
    assert "R91" in req and "kairos-hdmi-prime" in req and "Restart now" in req
    assert '"hdmi": hw.hdmi_state(),' in src, "/api/status carries the hdmi state (the shell polls it)"
    sh = (REPO / "shell" / "shell.js").read_text()
    assert "function hdmiNotice(" in sh and "function toastAction(" in sh and "hdmiNotice(s.hdmi)" in sh, "the consent toast"
    assert "Restart now" in sh and "Not now" in sh and "open windows will close" in sh and 'op: "hdmi"' in sh
    assert "restart the desktop" in disp and "later" in disp, "docs/api/display.md must describe the hdmi op (restart | later)"
    assert (REPO / "docs" / "HDMI-PRIME-2026-10-22.md").exists()
    return "settings key hdmi (on|off) wired; /api/status + the consent toast in the shell; display.md (hdmi op), system-settings.md, REQUIREMENTS R91, the dated report present"


# ------------------------------------------------------------------------------------------------------- the boot log
def bootlog_run(td, reason, state=None, planned=0, attempts=1, sessions=1):
    """the real kairos-bootlog on a fake ESP with a fake X layer: -> (latest.json, latest.txt)"""
    t = Path(td); esp = t / "esp"; runf = t / "run-attempt"
    xlog = t / "x.log"; xcnt = t / "x-attempts"; pl = t / "x-planned"; st = t / "hdmi-prime.json"
    xlog.write_text("".join(f"===== X attempt {i + 1}  now  uptime 7s =====\nxinitrc: started (pid 9, uptime 7s)\nxinitrc: providers: "
                            f"{PROVLINE_BOUND if (state or {}).get('state') == 'active' and i == sessions - 1 else 'AMD Radeon 780M Graphics @ pci:0000:05:00.0 unbound (source-output)'}\n"
                            f"xinitrc: displays: eDP connected primary 1920x1080+0+0;\n" for i in range(sessions)))
    xcnt.write_text(f"{attempts}\n"); pl.write_text(f"{planned}\n")
    if state:
        st.write_text(json.dumps(state))
    env = dict(os.environ, KAIROS_BOOTLOG_ESP=str(esp), KAIROS_BOOTLOG_RUN=str(runf), KAIROS_BOOTLOG_XLOG=str(xlog),
               KAIROS_BOOTLOG_XCNT=str(xcnt), KAIROS_BOOTLOG_PLANNED=str(pl), KAIROS_BOOTLOG_HDMI=str(st))
    subprocess.run([str(BOOTLOG), reason], env=env, check=True, timeout=170, capture_output=True)
    return json.loads((esp / "KAIROS-LOGS" / "latest.json").read_text()), (esp / "KAIROS-LOGS" / "latest.txt").read_text(errors="replace")


@test("hp_bootlog_hdmi_check_and_planned_restarts", ["R91", "R51"])
def t_bootlog():
    """kairos-bootlog: the `hdmi_prime` check (active -> PASS naming the connectors, failed -> WARN with the reason, no
    state -> PASS n/a), the HDMI/PRIME evidence section, and the planned restarts taken off the xorg check: a shutdown
    capture after one planned restart (2 X starts) says PASS, the same 2 starts without a plan still WARN"""
    subprocess.run(["sh", "-n", str(BOOTLOG)], check=True)
    body = BOOTLOG.read_text()
    assert 'HDMIST=${KAIROS_BOOTLOG_HDMI:-/run/kairos/hdmi-prime.json}' in body and "KAIROS_BOOTLOG_PLANNED" in body
    with tempfile.TemporaryDirectory() as td:
        j, txt = bootlog_run(td, "shutdown", {"state": "active", "reason": "NVIDIA-G0 is an output source of the desktop",
                                              "connectors": "DP-1=disconnected HDMI-A-1=connected"}, planned=1, attempts=2, sessions=2)
        assert j["checks"]["hdmi_prime"]["status"] == "PASS" and "HDMI-A-1=connected" in j["checks"]["hdmi_prime"]["detail"], j["checks"]["hdmi_prime"]
        assert j["checks"]["xorg"]["status"] == "PASS" and "1 planned restart(s) by kairos-hdmi-prime" in j["checks"]["xorg"]["detail"], j["checks"]["xorg"]
        assert "HDMI / reverse PRIME (kairos-hdmi-prime)" in txt and "NVIDIA-G0 bound" in txt
    with tempfile.TemporaryDirectory() as td:
        j, _ = bootlog_run(td, "shutdown", None, planned=0, attempts=2, sessions=2)
        assert j["checks"]["xorg"]["status"] == "WARN" and "restarted 1 time" in j["checks"]["xorg"]["detail"], j["checks"]["xorg"]
        assert j["checks"]["hdmi_prime"]["status"] == "PASS" and "left no state" in j["checks"]["hdmi_prime"]["detail"]
    with tempfile.TemporaryDirectory() as td:
        j, txt = bootlog_run(td, "shutdown", {"state": "failed", "reason": "the new X session has no NVIDIA-G0 provider",
                                              "connectors": "HDMI-A-1=connected"}, planned=2, attempts=3, sessions=3)
        assert j["checks"]["hdmi_prime"]["status"] == "WARN" and "no NVIDIA-G0 provider" in j["checks"]["hdmi_prime"]["detail"]
        assert j["checks"]["xorg"]["status"] == "PASS" and "3 start(s), 2 planned" in j["checks"]["xorg"]["detail"], j["checks"]["xorg"]
        assert '"state": "failed"' in txt
    return "hdmi_prime: active PASS / failed WARN / none PASS n/a; evidence section; xorg: 2 starts with 1 planned = PASS, without = WARN, 3 with 2 = PASS"


def main():
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    tests = [t for t in TESTS if not only or any(o in t._name for o in only)]
    print(f"kAIrOS HDMI on the NVIDIA (R91) — {len(tests)} tests", flush=True)
    for t in tests:
        asyncio.run(run_one(t))
    if not only:
        out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
        (REPO / "tests" / "results-hdmi-prime.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-hdmi-prime.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
