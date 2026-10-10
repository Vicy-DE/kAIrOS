"""kAIrOS boot timing [R73] — how long a boot takes, measured, so the boot screen's
loading bar is determinate instead of a guess.

The boot screen painter (scripts/splash/kairos-splash, started from the initramfs)
runs its progress against an EXPECTED boot time. That number is the rolling average of
the last HISTORY boots, each measured here as the uptime at which the kiosk loaded the
desktop shell (the first GET / of a boot = the moment the user sees the desktop), and
written to cache/boottime/expected on the root file system. The initramfs reads that
file (scripts/init-bottom/kairos-splash) and passes it to the painter through /run;
kairos-splash-ctl does the same from userspace as a fallback. Before any measurement
exists, DEFAULT_EXPECTED applies (the owner's v7.3 stick boot took ~2.5 min to the
desktop; an installed SSD boots in under a minute).

Replaces the boot-duration half of the R27 boot animation module (kernel/bootanim.py,
superseded 2026-10-07): the measurement stays, the dreamed video clip is gone. The
session log that fed it lives on in kernel/sessionlog.py for the R44 wallpaper.
"""
import json
from pathlib import Path

HISTORY = 5                  # boots averaged
DEFAULT_EXPECTED = 120.0     # seconds, until the first measured boot
MIN_EXPECTED, MAX_EXPECTED = 20.0, 600.0
NOT_A_BOOT_AFTER = 600.0     # a kernel start later than this is a restart mid-session, not a boot

_DIR: Path = None
_STATE = {"boot_seconds": None, "kernel_up": None}


def _uptime():
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return None


def init(vibeos_dir: Path):
    """Wire into the kernel (on_startup). Never raises: an unwritable cache dir only
    costs the measurement [R63]."""
    global _DIR
    _DIR = Path(vibeos_dir) / "cache" / "boottime"
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[boot] WARN cache dir {_DIR} unusable: {e}", flush=True)
    _STATE["kernel_up"] = _uptime()
    _STATE["boot_seconds"] = None
    if not (_DIR / "expected").exists():      # the next boot's initramfs needs a number even before the first measurement
        _write_expected(expected())


def history() -> list:
    try:
        hist = json.loads((_DIR / "boot-times.json").read_text())
        return [float(x) for x in hist if isinstance(x, (int, float))][-HISTORY:]
    except Exception:
        return []


def expected() -> float:
    hist = history()
    if not hist:
        return DEFAULT_EXPECTED
    avg = sum(hist) / len(hist)
    return round(min(MAX_EXPECTED, max(MIN_EXPECTED, avg)), 1)


def _write_expected(value: float):
    try:
        (_DIR / "expected").write_text(f"{value:.1f}\n")
    except OSError as e:
        print(f"[boot] WARN expected boot time not written: {e}", flush=True)


def desktop_ready(up: float = None):
    """The kiosk has loaded the desktop shell: this boot is over. Counts once per kernel
    start, and only within NOT_A_BOOT_AFTER of the kernel's start (a restarted kernel
    mid-session is not a boot). Returns the measured seconds, or None."""
    if _STATE["boot_seconds"] is not None or _DIR is None:
        return None
    if up is None:
        up = _uptime()
    if up is None or up > NOT_A_BOOT_AFTER:
        _STATE["boot_seconds"] = -1          # decided: not a boot — do not ask again
        return None
    secs = round(up, 1)
    _STATE["boot_seconds"] = secs
    hist = (history() + [secs])[-HISTORY:]
    try:
        (_DIR / "boot-times.json").write_text(json.dumps(hist))
    except OSError as e:
        print(f"[boot] WARN boot-time history not written: {e}", flush=True)
    _write_expected(expected() if history() else min(MAX_EXPECTED, max(MIN_EXPECTED, secs)))
    print(f"[boot] desktop ready after {secs}s; expected next boot {expected()}s (history {hist})", flush=True)
    return secs


def status() -> dict:
    b = _STATE["boot_seconds"]
    return {
        "this_boot_seconds": b if (b is not None and b >= 0) else None,
        "expected_seconds": expected(),
        "default_seconds": DEFAULT_EXPECTED,
        "history": history(),
        "kernel_up_seconds": _STATE["kernel_up"],
    }
