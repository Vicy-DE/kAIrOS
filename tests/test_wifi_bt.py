#!/usr/bin/env python3
"""kAIrOS WiFi & Bluetooth system API [R78] (docs/WIFI-BT-2026-10-07.md).

kernel/hw.py against STATEFUL mocks (tests/mock-nmcli, tests/mock-bluetoothctl incl. its
interactive pty mode, tests/mock-rfkill on a fake sysfs, a mock pactl + busctl written here):
nmcli escapes, the real SSID instead of the profile name, OWE / OWE transition mode without a
password, a wrong password that leaves no "X 1" profile and gets the previous network back,
error codes, radio / supplicant / NetworkManager states, the radio-state cache, the captive-portal
sign-in flags against a local portal, knowledge.online() following NetworkManager, Bluetooth
status shapes (old apps keep `connected` as names), a scan that lists only what THIS scan saw,
pair -> trust -> connect through the pty agent (just-works, numeric comparison, a keyboard's
code), rfkill on/off, the audio sink, airplane mode. Static: the R66 read/write split, the
bridge + both host pages, the docs the coder LLM gets (and their budget), the NetworkManager
connectivity conf, the .xinitrc sign-in window (run hermetically), the boot-log capture, R78.

Standalone: `python3 tests/test_wifi_bt.py` (run-tests.sh runs it after the suite). Writes
tests/results-wifi-bt.json (not for a KAIROS_TEST_ONLY subset). Needs no network and no root;
the portal + online tests and the server import need aiohttp (skip without).
"""
import asyncio
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INC = REPO / "iso" / "config" / "includes.chroot"
RESULTS = {}
TMP = Path(tempfile.mkdtemp(prefix="kairos-wifibt-"))
BIN = TMP / "bin"
SYS = TMP / "sys"
# BEFORE kernel/hw.py is imported: it reads the sysfs root and the flag paths at import time
os.environ.update(
    VIBEOS_SYSFS=str(SYS), MOCK_NMCLI_STATE=str(TMP / "nm.json"), MOCK_BT_STATE=str(TMP / "bt.json"),
    MOCK_NMCLI_LOG=str(TMP / "nm.log"), MOCK_BT_LOG=str(TMP / "bt.log"), MOCK_RFKILL_LOG=str(TMP / "rfkill.log"),
    KAIROS_OPEN_URL=str(TMP / "run" / "open-url"), KAIROS_CLOSE_URL=str(TMP / "run" / "close-url"),
    KAIROS_NM_CONF_DIRS=f"{TMP / 'usrlib'}:{TMP / 'etc'}", KAIROS_SIGNIN_POLL="0.2", KAIROS_SIGNIN_LIMIT="20",
    KAIROS_RUN_USER=str(TMP / "run-user"))
os.environ.pop("KAIROS_CONNECTIVITY_URI", None)
sys.path.insert(0, str(REPO / "kernel"))
import hw  # noqa: E402


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:150]}", flush=True)


class SkipTest(Exception):
    pass


def test(name, reqs):
    def deco(fn):
        def wrapper():
            try:
                setup()
                detail = fn()
                if asyncio.iscoroutine(detail):
                    detail = asyncio.run(detail)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                import traceback
                tb = traceback.extract_tb(e.__traceback__)
                where = next((f.lineno for f in reversed(tb) if f.filename == __file__), tb[-1].lineno)
                record(name, reqs, "fail", f"{type(e).__name__}: {e} @ {where}")
        wrapper._name, wrapper._reqs, wrapper._test = name, reqs, True
        return wrapper
    return deco


# ----------------------------------------------------------------------------- the mock machine
PACTL = r'''#!/usr/bin/env python3
import json, os, sys
st = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "..", "pactl.json")
w = json.load(open(st))
with open(os.path.join(os.path.dirname(st), "pactl.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
if a[:3] == ["list", "short", "sinks"]:
    for i, s in enumerate(w["sinks"]):
        print(f"{i + 50}\t{s}\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED")
elif a[:1] == ["get-default-sink"]:
    print(w["default"])
elif a[:1] == ["set-default-sink"]:
    w["default"] = a[1]; json.dump(w, open(st, "w"))
else:
    sys.exit(1)
'''
BUSCTL = r'''#!/usr/bin/env python3
import sys, time
if "LastScan" in sys.argv:
    print("x %d" % (time.clock_gettime(time.CLOCK_BOOTTIME) * 1000 - 12000))
else:
    sys.exit(1)
'''


def setup():
    """a clean machine for every test: mocks in TMP/bin, the default NM + bluez worlds, both
    radios unblocked, no flags, empty logs, hw.py's caches dropped"""
    BIN.mkdir(parents=True, exist_ok=True)
    for t in ("nmcli", "bluetoothctl", "rfkill"):
        if not (BIN / t).exists():
            (BIN / t).symlink_to(REPO / "tests" / f"mock-{t}")
    for name, src in (("pactl", PACTL), ("busctl", BUSCTL)):
        (BIN / name).write_text(src)
        (BIN / name).chmod(0o755)
    for i, (kind, name) in enumerate((("bluetooth", "hci0"), ("wlan", "phy0"))):
        d = SYS / "class" / "rfkill" / f"rfkill{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "type").write_text(kind + "\n")
        (d / "name").write_text(name + "\n")
        (d / "soft").write_text("0\n")
        (d / "hard").write_text("0\n")
    (SYS / "class" / "bluetooth" / "hci0").mkdir(parents=True, exist_ok=True)      # the adapter (btusb) exists
    for f in ("nm.json", "bt.json", "nm.log", "bt.log", "rfkill.log", "pactl.log", "pactl.json"):
        (TMP / f).unlink(missing_ok=True)
    for d in ("run", "usrlib", "etc"):
        shutil.rmtree(TMP / d, ignore_errors=True)
    (TMP / "pactl.json").write_text(json.dumps({"sinks": ["alsa_output.pci-0000_00_1f.3.analog-stereo"],
                                                "default": "alsa_output.pci-0000_00_1f.3.analog-stereo"}))
    hw.init(TMP)
    hw.invalidate()
    hw._PAIRING.clear()
    hw.RESTORE_DELAY = 0.3


def ap(ssid, signal, security="WPA2", psk=None, n=1, **kw):
    return {"ssid": ssid, "bssid": f"AA:BB:CC:00:00:{n:02X}", "chan": kw.pop("chan", 6), "freq": kw.pop("freq", 2437),
            "signal": signal, "security": security, "psk": psk, **kw}


def prof(name, ssid, uuid, psk=None, key_mgmt=None, ts=1759800000, auto=True):
    return {"name": name, "ssid": ssid, "uuid": uuid, "psk": psk or "", "key_mgmt": key_mgmt or ("wpa-psk" if psk else "none"),
            "autoconnect": auto, "timestamp": ts}


def nm_world(aps, profiles=(), active=None, **kw):
    w = {"nm_running": True, "radio": True, "supplicant_starting": False,
         "devices": [{"device": "wlan0", "type": "wifi", "path": "/org/freedesktop/NetworkManager/Devices/3",
                      "active": active, "ip": "10.42.0.23/24"},
                     {"device": "eth0", "type": "ethernet", "path": "/org/freedesktop/NetworkManager/Devices/2",
                      "state": "unavailable"}],
         "aps": list(aps), "profiles": list(profiles), **kw}
    (TMP / "nm.json").write_text(json.dumps(w))
    hw.invalidate()
    return w


def nm_state():
    return json.loads((TMP / "nm.json").read_text())


def bt_world(devices, powered=True, **kw):
    (TMP / "bt.json").write_text(json.dumps({"bluetoothd": True, "controller": {"mac": "AA:BB:CC:DD:EE:FF", "name": "kairos",
                                                                                 "powered": powered},
                                             "devices": list(devices), **kw}))
    hw.invalidate()


def bt_state():
    return json.loads((TMP / "bt.json").read_text())


def rfkill(kind, soft=None, hard=None):
    d = SYS / "class" / "rfkill" / ("rfkill0" if kind == "bluetooth" else "rfkill1")
    if soft is not None:
        (d / "soft").write_text(("1" if soft else "0") + "\n")
    if hard is not None:
        (d / "hard").write_text(("1" if hard else "0") + "\n")
    hw.invalidate()


def calls(log="nm.log"):
    p = TMP / log
    return [l.split("\t") for l in p.read_text().splitlines()] if p.exists() else []


def W(op, **body):
    return hw.handle({"device": "wifi", "op": op, **body})


def B(op, **body):
    return hw.handle({"device": "bluetooth", "op": op, **body})


HOME = "0b3f5a3e-0000-4000-8000-0000000000a1"


# ----------------------------------------------------------------------------- WiFi
@test("wifi_nmcli_escapes_roundtrip", ["R78"])
async def t_escapes():
    """nmcli -t -e yes escapes ':' and '\\' — the splitter undoes them; SSIDs with both survive
    scan, connect, status and forget (the old split(':') broke every BSSID-carrying row)"""
    assert hw._nm_split(r"a\:b:c\\d:") == ["a:b", "c\\d", ""]
    assert hw._nm_split(r"*:AA\:BB\:CC\:00\:00\:01:Cafe\:Bar:6:2437 MHz:70:WPA2") == \
        ["*", "AA:BB:CC:00:00:01", "Cafe:Bar", "6", "2437 MHz", "70", "WPA2"]
    assert hw._nm_split("trailing\\") == ["trailing\\"]
    nm_world([ap("Cafe:Bar", 70, "", n=1), ap("back\\slash 5G", 60, "WPA2", "secret-pass", n=2, freq=5180, chan=36)])
    sc = await W("scan")
    assert [n["ssid"] for n in sc["networks"]] == ["Cafe:Bar", "back\\slash 5G"], sc
    r = await W("connect", ssid="Cafe:Bar")
    assert r["ok"] and r["ssid"] == "Cafe:Bar", r
    st = await W("status")
    assert st["connected"] and st["ssid"] == "Cafe:Bar" and st["devices"][0]["connection"] == "Cafe:Bar", st
    r = await W("connect", ssid="back\\slash 5G", password="secret-pass")
    assert r["ok"], r
    assert (await W("status"))["ssid"] == "back\\slash 5G"
    f = await W("forget", ssid="Cafe:Bar")
    assert f["ok"] and f["deleted"] == 1 and not [p for p in nm_state()["profiles"] if p["ssid"] == "Cafe:Bar"], f
    return "':' and '\\' survive scan/connect/status/forget; BSSID rows split right"


@test("wifi_needs_bars_band", ["R78"])
def t_needs():
    """the render-ready fields: needs from nmcli's SECURITY (the stick run's real strings), bars
    like nmcli's BARS, band from the frequency"""
    table = {"": "none", "--": "none", "OWE": "none", "OWE-TM": "none", "WPA2": "password", "WPA3": "password",
             "WPA2 WPA3": "password", "WPA1 WPA2": "password", "WEP": "password", "WPA2 802.1X": "login",
             "WPA1 WPA2 802.1X": "login", "WPA3 802.1X": "login", "WAPI": "unsupported"}
    for sec, need in table.items():
        assert hw.wifi_needs(sec) == need, (sec, hw.wifi_needs(sec), need)
    assert [hw._bars(s) for s in (0, 5, 6, 30, 31, 55, 56, 80, 81, 100)] == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    assert (hw._band("2412 MHz"), hw._band("5180 MHz"), hw._band("5955 MHz"), hw._band("")) == ("2.4", "5", "6", None)
    return f"{len(table)} security strings (OWE/OWE-TM = none, 802.1X = login) · bars 0-4 · bands 2.4/5/6"


@test("wifi_status_real_ssid_disconnect_forget", ["R78", "R38"])
async def t_real_ssid():
    """status().ssid is the network's SSID, not the profile NAME (profile 'Home profile 1' on
    'HomeNet'); disconnect() needs no SSID; forget(ssid) deletes every profile of that SSID; a
    profile NAME (what old apps passed) still works"""
    nm_world([ap("HomeNet", 77, "WPA2", "home-secret", n=1), ap("HomeNet", 91, "WPA2", "home-secret", n=2, freq=5500, chan=100)],
             [prof("Home profile 1", "HomeNet", HOME, "home-secret"),
              prof("HomeNet", "HomeNet", "0b3f5a3e-0000-4000-8000-0000000000a2", "home-secret", ts=1759700000),
              prof("legacy-name", "OtherNet", "0b3f5a3e-0000-4000-8000-0000000000a3", "x" * 8)], active=HOME)
    st = await W("status")
    assert st["available"] and st["enabled"] and st["connected"] and st["ssid"] == "HomeNet", st
    assert st["devices"] == [{"device": "wlan0", "state": "connected", "connection": "Home profile 1"}], st["devices"]
    assert st["ip"] == "10.42.0.23" and st["connectivity"] == "full" and st["device"] == "wlan0", st
    assert st["signal"] in (77, 91) and st["bars"] == hw._bars(st["signal"]), st
    sc = await W("scan")
    home = [n for n in sc["networks"] if n["ssid"] == "HomeNet"]
    assert len(home) == 1 and home[0]["signal"] == 91 and home[0]["band"] == "5" and home[0]["saved"] and home[0]["in_use"], sc
    sv = await W("saved")
    assert [n["ssid"] for n in sv["networks"]] == ["HomeNet", "OtherNet"], sv
    d = await W("disconnect", ssid="whatever-old-apps-pass")
    assert d["ok"], d
    st = await W("status")
    assert not st["connected"] and st["ssid"] is None and st["connectivity"] == "none", st
    f = await W("forget", ssid="HomeNet")
    assert f["ok"] and f["deleted"] == 2, f
    f2 = await W("forget", ssid="legacy-name")
    assert f2["ok"] and f2["deleted"] == 1 and nm_state()["profiles"] == [], (f2, nm_state()["profiles"])
    f3 = await W("forget", ssid="HomeNet")
    assert not f3["ok"] and f3["error"] == "not_saved", f3
    return "ssid HomeNet (profile 'Home profile 1'), strongest BSS 91 on 5 GHz, disconnect w/o ssid, forget 2 profiles + by name"


@test("wifi_owe_transition_no_password", ["R78"])
async def t_owe():
    """Freifunk's OWE transition mode: an open SSID marked OWE-TM + a hidden OWE BSS. Listed once,
    needs none, joined WITHOUT a password; once joined the hidden twin shows its own SSID — it is
    folded into the joined network instead of a second entry"""
    # what NetworkManager 1.52 really lists for such a pair (VM proof W9): the hidden OWE BSS appears
    # with no SSID (the in-use row once joined), with the transition SSID and with its learned name
    nm_world([ap("ffm.freifunk.net", 66, "OWE-TM", n=1, freq=2412, chan=1, owe_twin="owe.ffm.freifunk.net"),
              {**ap("", 64, "OWE", n=2, freq=2412, chan=1), "hidden": True, "real_ssid": "owe.ffm.freifunk.net"},
              ap("ffm.freifunk.net", 64, "OWE", n=2, freq=2412, chan=1), ap("owe.ffm.freifunk.net", 64, "OWE", n=2, freq=2412, chan=1),
              ap("OpenAP", 50, "WPA2 WPA3", "pw-pw-pw-pw", n=3)])
    sc = await W("scan")
    ff = [n for n in sc["networks"] if "freifunk" in n["ssid"]]
    assert len(ff) == 1 and ff[0]["ssid"] == "ffm.freifunk.net" and ff[0]["needs"] == "none" and ff[0]["security"] == "OWE-TM", sc
    assert hw._nm_detail("\x1b[2K\rWarning: x\nError: Connection activation failed: Secrets were required\n", "pw") \
        == "Error: Connection activation failed: Secrets were required"
    assert hw._nm_detail("\x1b[2K\rDevice 'wlan0' successfully activated with 'u'.\n") == "Device 'wlan0' successfully activated with 'u'."
    assert next(n for n in sc["networks"] if n["ssid"] == "OpenAP")["needs"] == "password"
    r = await W("connect", ssid="ffm.freifunk.net", password="the app asked anyway")
    assert r["ok"], r
    joined = [c for c in calls() if "connect" in c and "wifi" in c]
    assert joined and "password" not in joined[-1], joined
    st = await W("status")
    assert st["connected"] and st["ssid"] == "ffm.freifunk.net", st
    sc = await W("scan")
    ff = [n for n in sc["networks"] if "freifunk" in n["ssid"]]
    assert len(ff) == 1 and ff[0]["ssid"] == "ffm.freifunk.net" and ff[0]["in_use"], sc["networks"]
    return "OWE-TM listed once (needs none), joined without a password, the hidden owe.* twin folded into it"


@test("wifi_wrong_password_no_stale_profile", ["R78"])
async def t_wrong_pw():
    """a wrong password: error wrong_password, NO profile left behind (NetworkManager keeps 'X',
    then 'X 1' …), the previous network is back, the password never reaches the kernel log; the
    right one then joins and status shows the new SSID"""
    nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1), ap("Neighbors5G", 62, "WPA2", "neighbors-secret", n=2, freq=5180)],
             [prof("OpenAP", "OpenAP", HOME, "pw-home-123")], active=HOME)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for _ in range(3):
            r = await W("connect", ssid="Neighbors5G", password="hunter2-wrong")
            assert not r["ok"] and r["error"] == "wrong_password" and "hunter2-wrong" not in json.dumps(r), r
            await asyncio.sleep(0.5)
    names = [p["name"] for p in nm_state()["profiles"]]
    assert names == ["OpenAP"], f"stale profiles: {names}"
    assert "hunter2-wrong" not in buf.getvalue() and "removed 1 profile" in buf.getvalue(), buf.getvalue()
    st = await W("status")
    assert st["connected"] and st["ssid"] == "OpenAP", st
    r = await W("connect", ssid="Neighbors5G", password="neighbors-secret")
    assert r["ok"] and r["connectivity"] == "full", r
    st = await W("status")
    assert st["ssid"] == "Neighbors5G" and [p["name"] for p in nm_state()["profiles"]] == ["OpenAP", "Neighbors5G"], st
    return "3x wrong password: wrong_password, 0 stale profiles, back on OpenAP, password not logged; right one joins"


@test("wifi_saved_profile_password_rollback", ["R78"])
async def t_rollback():
    """a saved network: connect() without a password uses the stored one; a NEW password updates
    the profile and is rolled back when it is wrong (the working one stays); already connected
    and no password -> ok at once"""
    nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1)], [prof("OpenAP", "OpenAP", HOME, "pw-home-123")], active=HOME)
    r = await W("connect", ssid="OpenAP")
    assert r["ok"] and r["detail"] == "already connected", r
    r = await W("connect", ssid="OpenAP", password="typo-typo-typo")
    assert not r["ok"] and r["error"] == "wrong_password", r
    p = next(p for p in nm_state()["profiles"] if p["uuid"] == HOME)
    assert p["psk"] == "pw-home-123", f"the working password must be restored, got {p['psk']!r}"
    await asyncio.sleep(0.6)
    assert (await W("status"))["ssid"] == "OpenAP"
    assert (await W("disconnect"))["ok"]
    r = await W("connect", ssid="OpenAP")
    assert r["ok"] and r["detail"] != "already connected", r
    ups = [c for c in calls() if c[:1] == ["--wait"] and "up" in c]
    assert ups and HOME in ups[-1], ups
    assert len(nm_state()["profiles"]) == 1
    return "saved psk reused, wrong new psk rolled back, re-join via `con up` of the same profile"


@test("wifi_error_codes", ["R78"])
async def t_errors():
    """the documented error codes: needs_password, not_in_range, unsupported (802.1X), ssid_required,
    wrong_password for a password NetworkManager rejects as invalid — never a stale profile"""
    nm_world([ap("Secured", 70, "WPA2", "long-enough-pw", n=1), ap("eduroam", 60, "WPA2 802.1X", n=2)], validate_psk=True)
    r = await W("connect", ssid="Secured")
    assert r["error"] == "needs_password" and not r["ok"], r
    r = await W("connect", ssid="Nowhere")
    assert r["error"] == "not_in_range", r
    r = await W("connect", ssid="eduroam", password="x")
    assert r["error"] == "unsupported" and "802.1X" in r["detail"], r
    r = await W("connect", ssid="")
    assert r["error"] == "ssid_required", r
    r = await W("connect", ssid="Secured", password="short")
    assert r["error"] == "wrong_password", r
    assert nm_state()["profiles"] == [], nm_state()["profiles"]
    assert set(hw.WIFI_ERRORS) >= {"wrong_password", "not_in_range", "timeout", "needs_password", "unsupported"}
    assert hw._nm_error("Error: Timeout expired (45 seconds)", True) == "timeout"
    assert hw._nm_error("nmcli timed out after 60s", False) == "timeout"
    return "needs_password / not_in_range / unsupported / ssid_required / wrong_password (invalid psk), 0 profiles"


@test("wifi_restore_previous_network", ["R78"])
async def t_restore():
    """when NetworkManager does NOT bring the previous network back after a failed join, kAIrOS
    re-activates it (RESTORE_DELAY later) — unless a newer connect() started meanwhile"""
    nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1), ap("Cafe", 60, "WPA2", "cafe-cafe-cafe", n=2)],
             [prof("OpenAP", "OpenAP", HOME, "pw-home-123")], active=HOME, no_autoreconnect=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = await W("connect", ssid="Cafe", password="wrong-wrong")
        assert r["error"] == "wrong_password", r
        assert not (await W("status"))["connected"], "the mock world must leave the device off-line"
        await asyncio.sleep(1.5)
    st = await W("status")
    assert st["connected"] and st["ssid"] == "OpenAP", (st, buf.getvalue())
    assert "re-joined the previous network" in buf.getvalue(), buf.getvalue()
    # the user's disconnect() right after a failed join: the pending restore must not undo it
    hw.RESTORE_DELAY = 1.0
    r = await W("connect", ssid="Cafe", password="wrong-wrong")
    assert r["error"] == "wrong_password" and not (await W("status"))["connected"]
    await W("disconnect")
    await asyncio.sleep(1.5)
    assert not (await W("status"))["connected"], "a restore undid the user's disconnect"
    await W("connect", ssid="OpenAP")
    # a newer connect() wins: the restore of the first failure must not undo it
    hw.RESTORE_DELAY = 1.0
    r = await W("connect", ssid="Cafe", password="wrong-wrong")
    r2 = await W("connect", ssid="Cafe", password="cafe-cafe-cafe")
    assert r2["ok"], r2
    await asyncio.sleep(1.5)
    assert (await W("status"))["ssid"] == "Cafe"
    return "previous network re-joined after the failed join; a newer connect() is never undone"


@test("wifi_radio_supplicant_nm_states", ["R78"])
async def t_states():
    """radio off / on, the supplicant window at boot (answer, don't fail), NetworkManager not
    running, a hardware switch, an older nmcli without IP4-CONNECTIVITY: every status/scan answers"""
    nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1)], [prof("OpenAP", "OpenAP", HOME, "pw-home-123")], active=HOME)
    r = await W("off")
    assert r["ok"] and r["enabled"] is False, r
    st = await W("status")
    assert st["available"] and st["enabled"] is False and not st["connected"], st
    sc = await W("scan")
    assert sc["enabled"] is False and sc["networks"] == [] and "on()" in sc["reason"], sc
    r = await W("on")
    assert r["ok"] and (await W("status"))["connected"], r
    nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1)], supplicant_starting=True)
    st = await W("status")
    assert st["available"] and st["enabled"] and st.get("starting") and not st["connected"], st
    sc = await W("scan")
    assert sc["available"] and sc.get("starting") and sc["networks"] == [], sc
    t0 = time.monotonic()
    r = await W("connect", ssid="OpenAP", password="pw-home-123")    # waits ~12 s for the supplicant, then says so
    assert not r["ok"] and r["error"] == "timeout" and "still starting" in r["detail"] and time.monotonic() - t0 >= 10, r
    nm_world([], nm_running=False)
    st = await W("status")
    assert st["available"] is False and st["reason"] == "NetworkManager is not running" and st["devices"] == [], st
    assert (await W("scan"))["available"] is False
    nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1)], [prof("OpenAP", "OpenAP", HOME, "pw-home-123")], active=HOME)
    rfkill("wlan", hard=True)
    st = await W("status")
    assert st["enabled"] is False and "hardware switch" in st["reason"], st
    r = await W("connect", ssid="OpenAP")
    assert not r["ok"] and "hardware switch" in r["detail"], r
    rfkill("wlan", hard=False)
    nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1)], [prof("OpenAP", "OpenAP", HOME, "pw-home-123")], active=HOME,
             old_nmcli=True)
    st = await W("status")
    assert st["connected"] and st["ssid"] == "OpenAP" and st["connectivity"] == "unknown", st
    return "radio off/on, supplicant starting (starting:true), NM down (reason), hardware switch, old nmcli fallback"


@test("wifi_scan_dedupe_rescan_cache", ["R78"])
async def t_scan_cache():
    """one entry per SSID (strongest BSS), hidden BSSs dropped, sorted by signal, 6 GHz band, the
    cached list unless fresh (a network only a new scan finds), age_s from NetworkManager's
    LastScan; status() is cached ~2 s (a 5-s poll spawns nmcli once) and every write clears it
    and tells the listeners (knowledge.online's cache)"""
    nm_world([ap("A", 40, n=1), ap("A", 75, n=2, freq=5955, chan=1), {**ap("", 90, "WPA2", n=3), "hidden": True},
              ap("B", 60, "", n=4), {**ap("Late", 30, "", n=5), "after_rescan": True}])
    sc = await W("scan")
    assert [(n["ssid"], n["signal"], n["band"]) for n in sc["networks"]] == [("A", 75, "6"), ("B", 60, "2.4")], sc
    assert 10 <= sc["age_s"] <= 14, sc["age_s"]
    rescans = lambda: [c for c in calls() if "--rescan" in c and c[c.index("--rescan") + 1] == "yes"]
    assert not rescans(), "the cached list needs no rescan"
    sc = await W("scan", fresh=True)
    assert "Late" in [n["ssid"] for n in sc["networks"]] and len(rescans()) == 1, (sc, rescans())
    (TMP / "nm.log").unlink()
    await W("status")
    n1 = len(calls())
    await W("status")
    await hw.summary()
    assert len(calls()) == n1, f"cached status spawned nmcli again ({n1} -> {len(calls())})"
    seen = []
    hw.ON_CHANGE.append(lambda: seen.append(1))
    try:
        await W("connect", ssid="B")
    finally:
        hw.ON_CHANGE.pop()
    assert seen, "a write op must notify the listeners"
    assert (await W("status"))["ssid"] == "B", "the cache must be cleared by a write"
    return f"dedupe/hidden/sort/6 GHz OK; fresh scan finds 'Late'; age_s {sc['age_s']}; cached status ({n1} spawns once); write clears + notifies"


def _portal_server(port_box, mode):
    """a tiny local captive portal / check endpoint: 302 to the login page, or the real answer"""
    from aiohttp import web

    async def check(request):
        if mode["portal"]:
            return web.Response(status=302, headers={"Location": "http://login.portal.test/?next=hotspot"})
        return web.Response(text="NetworkManager is online\n")
    app = web.Application()
    app.router.add_get("/nm", check)
    return app


@test("wifi_portal_signin_window", ["R78"])
async def t_portal():
    """a hotspot behind a captive portal: connectivity:"portal" per device; signin() fetches the
    check URI WITHOUT following the redirect, writes the login URL into the open-url flag for the
    kiosk supervisor and, once the device is online, the close flag; no portal -> neverssl"""
    try:
        from aiohttp import web
    except ImportError:
        raise SkipTest("aiohttp not installed")
    mode = {"portal": True}
    runner = web.AppRunner(_portal_server(None, mode))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        (TMP / "usrlib").mkdir()
        (TMP / "etc").mkdir()
        (TMP / "usrlib" / "20-connectivity-debian.conf").write_text("[connectivity]\nuri=http://wrong.example/nm\n")
        (TMP / "etc" / "20-kairos-connectivity.conf").write_text(
            f"[connectivity]\nuri=http://127.0.0.1:{port}/nm\nresponse=NetworkManager is online\ninterval=300\n")
        assert hw._check_config() == (f"http://127.0.0.1:{port}/nm", "NetworkManager is online")
        nm_world([ap("WIFIonICE", 80, "", n=1, connectivity="portal")])
        r = await W("signin")
        assert not r["ok"] and r["error"] == "not_connected", r
        r = await W("connect", ssid="WIFIonICE")
        assert r["ok"] and r["connectivity"] == "portal", r
        st = await W("status")
        assert st["connectivity"] == "portal", st
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = await W("signin")
            assert r["ok"] and r["portal"] is True and r["url"] == "http://login.portal.test/?next=hotspot", r
            flag = Path(os.environ["KAIROS_OPEN_URL"])
            assert flag.read_text().strip() == r["url"] and oct(flag.stat().st_mode)[-3:] == "666", flag
            w = nm_state()
            w["aps"][0]["connectivity"] = "full"
            (TMP / "nm.json").write_text(json.dumps(w))
            close = Path(os.environ["KAIROS_CLOSE_URL"])
            for _ in range(40):
                if close.exists():
                    break
                await asyncio.sleep(0.1)
            assert close.exists(), "online -> the sign-in window must be closed"
        assert "the device is online" in buf.getvalue(), buf.getvalue()
        mode["portal"] = False
        flag.unlink()
        r = await W("signin")
        assert r["ok"] and r["portal"] is False and r["url"] == hw.PORTAL_FALLBACK and not close.exists(), r
        hw._SIGNIN["task"].cancel()
    finally:
        await runner.cleanup()
    return f"portal per device; signin -> 302 Location into the open-url flag (0666), close flag once online; no portal -> {hw.PORTAL_FALLBACK}"


@test("wifi_connectivity_conf_precedence", ["R78"])
def t_conf():
    """NetworkManager's conf.d order: /usr/lib then /etc by file name, a later value wins, a file
    of the same name in /etc masks the one in /usr/lib, enabled=false switches the check off"""
    (TMP / "usrlib").mkdir()
    (TMP / "etc").mkdir()
    (TMP / "usrlib" / "20-connectivity-debian.conf").write_text("[connectivity]\nuri=http://a/nm\n")
    assert hw._check_config() == ("http://a/nm", "NetworkManager is online")
    (TMP / "etc" / "20-kairos-connectivity.conf").write_text("[connectivity]\nuri=http://b/nm\nresponse=yes\n")
    assert hw._check_config() == ("http://b/nm", "yes")
    (TMP / "etc" / "20-connectivity-debian.conf").write_text("[main]\n")       # masks /usr/lib's file
    (TMP / "etc" / "20-kairos-connectivity.conf").unlink()
    assert hw._check_config()[0] is None
    (TMP / "etc" / "99-off.conf").write_text("[connectivity]\nuri=http://c/nm\nenabled=false\n")
    assert hw._check_config()[0] is None
    shipped = (INC / "etc" / "NetworkManager" / "conf.d" / "20-kairos-connectivity.conf").read_text()
    assert re.search(r"^\[connectivity\]$", shipped, re.M) and "uri=http://network-test.debian.org/nm" in shipped \
        and "response=NetworkManager is online" in shipped and re.search(r"^interval=300$", shipped, re.M), shipped
    return "usr/lib < etc by basename, masking, enabled=false; the shipped conf = Debian's check URI + response + 300 s"


@test("knowledge_online_follows_nm", ["R78"])
async def t_online():
    """knowledge.online(): NetworkManager 'full' -> online without the DuckDuckGo probe, 'none' ->
    offline at once, portal/limited/unknown -> the probe; cached 30 s, reset_online() clears it;
    hw.nm_connectivity() reads the global state (None without NetworkManager)"""
    try:
        import knowledge
    except ImportError:
        raise SkipTest("aiohttp not installed")
    probes = []

    class NoNet:
        def __init__(self, *a, **k):
            probes.append(1)
            raise OSError("no network in the test")
    saved_env, saved_cs = os.environ.pop("VIBEOS_FORCE_OFFLINE", None), knowledge.aiohttp.ClientSession
    knowledge.aiohttp.ClientSession = NoNet
    try:
        kb = knowledge.Knowledge(TMP)
        state = {"c": "full"}

        async def conn():
            return state["c"]
        kb.connectivity = conn
        assert await kb.online() is True and not probes
        state["c"] = "none"
        assert await kb.online() is True, "cached 30 s"
        kb.reset_online()
        assert await kb.online() is False and not probes
        state["c"] = "portal"
        kb.reset_online()
        assert await kb.online() is False and len(probes) == 1, "a portal is no web: the probe decides"
        nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1)], [prof("OpenAP", "OpenAP", HOME, "pw-home-123")], active=HOME)
        assert await hw.nm_connectivity() == "full"
        nm_world([], nm_running=False)
        assert await hw.nm_connectivity() is None
    finally:
        knowledge.aiohttp.ClientSession = saved_cs
        if saved_env is not None:
            os.environ["VIBEOS_FORCE_OFFLINE"] = saved_env
    return "full -> online (no probe), none -> offline, portal -> probe; 30-s cache + reset_online(); nm_connectivity full/None"


# ----------------------------------------------------------------------------- Bluetooth
MOUSE = {"mac": "C0:FF:EE:00:00:01", "name": "MX Master 2S", "icon": "input-mouse", "in_range": True, "rssi": -48,
         "pairing": "justworks", "battery": 70}
BUDS = {"mac": "C0:FF:EE:00:00:02", "name": "MeloBuds N70", "icon": "audio-headset", "in_range": True, "rssi": -60,
        "pairing": "justworks"}
PHONE = {"mac": "C0:FF:EE:00:00:03", "name": "Pixel", "icon": "phone", "in_range": True, "rssi": -70,
         "pairing": "confirm", "passkey": "314159"}
KEYB = {"mac": "C0:FF:EE:00:00:04", "name": "K380", "icon": "input-keyboard", "in_range": True, "rssi": -55,
        "pairing": "display", "passkey": "482913", "type_after": 1.0}


@test("bt_status_shapes", ["R55", "R78"])
async def t_bt_status():
    """status(): `connected` stays a list of NAMES (apps generated before R78 print it), plus
    blocked and devices [{mac, name, kind, paired, trusted, connected, battery}]; no adapter / no
    bluetoothd answer with available:false + a reason"""
    bt_world([{"mac": "11:22:33:44:55:66", "name": "Mock Headphones", "icon": "audio-headphones", "paired": True,
               "trusted": True, "connected": True, "battery": 80, "in_range": True, "rssi": -50},
              {"mac": "77:88:99:AA:BB:CC", "name": "Mock Speaker", "icon": "audio-card", "known": True}])
    st = await B("status")
    assert st["available"] and st["powered"] and st["blocked"] is False and st["connected"] == ["Mock Headphones"], st
    assert st["devices"] == [{"mac": "11:22:33:44:55:66", "name": "Mock Headphones", "kind": "audio", "paired": True,
                              "trusted": True, "connected": True, "battery": 80}], st["devices"]
    (TMP / "bt.json").write_text(json.dumps({"bluetoothd": True, "controller": None, "devices": []}))
    hw.invalidate()
    st = await B("status")
    assert st["available"] is False and st["reason"] == "no Bluetooth adapter found" and st["connected"] == [], st
    (TMP / "bt.json").write_text(json.dumps({"bluetoothd": False, "devices": []}))
    hw.invalidate()
    st = await B("status")
    assert st["available"] is False and "not answering" in st["reason"], st
    assert [hw.bt_kind(i) for i in ("audio-headset", "input-mouse", "input-keyboard", "input-gaming", "phone", "computer", "")] \
        == ["audio", "mouse", "keyboard", "gamepad", "phone", "other", "other"]
    # no controller in the kernel at all (a VM, a desktop): answered at once — bluetoothctl would
    # wait for a bluetoothd that never starts (6 s per status in the VM proof)
    (SYS / "class" / "bluetooth" / "hci0").rmdir()
    hw.invalidate()
    (TMP / "bt.log").unlink(missing_ok=True)
    t0 = time.monotonic()
    st, sc, on = await B("status"), await B("scan"), await B("on")
    assert st["available"] is False and st["reason"] == hw.NO_ADAPTER and st["connected"] == [] and sc["devices"] == [], (st, sc)
    assert on["ok"] is False and on["detail"] == hw.NO_ADAPTER and not calls("bt.log") and time.monotonic() - t0 < 1, (on, calls("bt.log"))
    return "connected = names, devices with kind/trusted/battery; no adapter (sysfs, no spawn) / no bluetoothd -> reasons; kinds from Icon"


@test("bt_scan_only_this_scan", ["R78"])
async def t_bt_scan():
    """scan(): only devices seen in THIS scan, not paired, with a name, strongest first — not the
    whole bluez cache (the old scan listed every device ever seen); a read: never powers the adapter"""
    bt_world([{**MOUSE, "paired": True, "trusted": True, "connected": True},
              {"mac": "C0:FF:EE:00:00:09", "name": "Neighbour TV", "icon": "video-display", "known": True, "in_range": False},
              BUDS, KEYB, {"mac": "C0:FF:EE:00:00:0A", "in_range": True, "rssi": -40}])
    try:
        sc = await B("scan", seconds=1)
        assert sc["available"] and [d["name"] for d in sc["devices"]] == ["K380", "MeloBuds N70"], sc
        assert [d["kind"] for d in sc["devices"]] == ["keyboard", "audio"] and sc["devices"][0]["rssi"] == -55, sc
        bt_world([BUDS], powered=False)
        sc = await B("scan", seconds=1)
        assert sc["powered"] is False and sc["devices"] == [] and "on()" in sc["reason"], sc
        assert not [c for c in calls("bt.log") if c[:1] == ["power"]], "a scan must not switch the adapter on"
        rfkill("bluetooth", soft=True)
        sc = await B("scan", seconds=1)
        assert sc["blocked"] and sc["devices"] == [], sc
    finally:
        await hw.AGENT.stop()
    return "2 of 5 listed (paired, out-of-range cache, unnamed dropped), sorted by RSSI; off/blocked: no power-on"


@test("bt_pair_trust_connect", ["R78"])
async def t_bt_pair():
    """pair() through the pty agent: pair -> trust -> connect (trust = reconnects after a reboot);
    numeric comparison is confirmed and its code returned; failures map to codes"""
    bt_world([MOUSE, PHONE, {**BUDS, "pairing": "fail"}, {"mac": "C0:FF:EE:00:00:05", "name": "Far", "in_range": False,
                                                          "known": True},
              {"mac": "C0:FF:EE:00:00:06", "name": "Twin", "known": True}, {"mac": "C0:FF:EE:00:00:07", "name": "Twin", "known": True}])
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            await B("scan", seconds=1)
            r = await B("pair", mac=MOUSE["mac"].lower())
        assert r["ok"] and r["paired"] and r["trusted"] and r["connected"] and r["kind"] == "mouse" and r["mac"] == MOUSE["mac"], r
        d = next(x for x in bt_state()["devices"] if x["mac"] == MOUSE["mac"])
        assert d["paired"] and d["trusted"] and d["connected"], d
        st = await B("status")
        assert "MX Master 2S" in st["connected"] and next(x for x in st["devices"] if x["mac"] == MOUSE["mac"])["trusted"], st
        assert "bt-agent: bluetoothctl pid" in buf.getvalue(), buf.getvalue()
        r = await B("pair", mac=PHONE["mac"])
        assert r["ok"] and r["code"] == "314159", r
        r = await B("pair", mac=BUDS["mac"])
        assert not r["ok"] and r["error"] == "auth_failed", r
        r = await B("pair", mac="C0:FF:EE:00:00:05")
        assert not r["ok"] and r["error"] == "not_in_range", r
        r = await B("pair", mac="not-a-mac")
        assert r["error"] == "bad_mac", r
        r = await B("connect", mac="nope")
        assert r["error"] == "bad_mac", r
        # GPU run 4: Gemma's Disconnect button sent the NAME (status().connected lists names) — resolved,
        # exact + case-insensitive, one match only
        r = await B("disconnect", mac="mx master 2s")
        assert r["ok"] and r["mac"] == MOUSE["mac"], r
        assert not next(x for x in bt_state()["devices"] if x["mac"] == MOUSE["mac"])["connected"]
        r = await B("remove", mac="Twin")
        assert not r["ok"] and r["error"] == "bad_mac", r
    finally:
        await hw.AGENT.stop()
    return "mouse paired+trusted+connected, phone code 314159 confirmed, auth_failed / not_in_range / bad_mac; disconnect by name"


@test("bt_pair_keyboard_code", ["R78"])
async def t_bt_keyboard():
    """a keyboard shows nothing — kAIrOS shows the passkey to type: pair() answers enter_code +
    code at once and finishes in the background; status().pairing follows; a second pair() while
    waiting says so"""
    bt_world([KEYB])
    try:
        await B("scan", seconds=1)
        r = await B("pair", mac=KEYB["mac"])
        assert not r["ok"] and r["error"] == "enter_code" and r["code"] == "482913" and r["pending"], r
        r2 = await B("pair", mac=KEYB["mac"])
        assert r2["error"] == "enter_code" and r2["pending"], r2
        st = await B("status")
        assert st["pairing"]["state"] == "enter_code" and st["pairing"]["code"] == "482913", st
        for _ in range(60):
            await asyncio.sleep(0.1)
            if hw._PAIRING.get("state") == "paired":
                break
        st = await B("status")
        assert st["pairing"]["state"] == "paired", st.get("pairing")
        d = next(x for x in st["devices"] if x["mac"] == KEYB["mac"])
        assert d["paired"] and d["trusted"] and d["kind"] == "keyboard", d
    finally:
        await hw.AGENT.stop()
    return "enter_code + 482913 at once, busy while waiting, status().pairing -> paired, trusted keyboard"


@test("bt_agent_refuses_unsolicited", ["R78"])
def t_bt_agent():
    """the agent answers prompts only while one of OUR pair() calls runs: an unsolicited
    'Confirm passkey' / 'Accept pairing' from a stranger gets "no"; a keyboard's passkey is shown"""
    a = hw._BtAgent()
    sent = []
    a.send = sent.append
    a.text = "[agent] Confirm passkey 999999 (yes/no): [agent] Accept pairing (yes/no): "
    with contextlib.redirect_stdout(io.StringIO()):
        a._prompts()
        assert sent == ["no", "no"], sent
        a.armed_until = time.monotonic() + 10
        a.text += "[agent] Confirm passkey 123456 (yes/no): [agent] Passkey: 482913\n"
        a._prompts()
    assert sent == ["no", "no", "yes"] and a.code == "482913", (sent, a.code)
    return "unarmed: no/no; armed: yes; displayed passkey surfaced"


@test("bt_rfkill_on_off", ["R78"])
async def t_bt_power():
    """off() = rfkill block (systemd-rfkill restores it at boot; bluez AutoEnable undid a power off),
    status says blocked; on() = unblock + power on; a hardware switch cannot be undone"""
    bt_world([])
    r = await B("off")
    assert r["ok"] and r["blocked"] and (SYS / "class/rfkill/rfkill0/soft").read_text().strip() == "1", r
    assert (SYS / "class/rfkill/rfkill1/soft").read_text().strip() == "0", "only Bluetooth"
    st = await B("status")
    assert st["available"] and st["blocked"] and st["powered"] is False and "on()" in st["reason"], st
    r = await B("on")
    assert r["ok"] and r["powered"] and (SYS / "class/rfkill/rfkill0/soft").read_text().strip() == "0", r
    assert (await B("status"))["powered"]
    rfkill("bluetooth", hard=True)
    r = await B("on")
    assert not r["ok"] and "hardware switch" in r["detail"], r
    return "off = rfkill block (status blocked), on = unblock + power on, hard block refused"


@test("bt_audio_sink_default", ["R78"])
async def t_bt_audio():
    """headphones connected: their PipeWire sink is reported and becomes the default — by
    WirePlumber itself (default_by wireplumber) or by kAIrOS (default_by kairos)"""
    sink = "bluez_output." + BUDS["mac"].replace(":", "_") + ".1"
    bt_world([BUDS])
    (TMP / "pactl.json").write_text(json.dumps({"sinks": ["alsa_output.pci.analog-stereo", sink],
                                                "default": "alsa_output.pci.analog-stereo"}))
    try:
        await B("scan", seconds=1)
        r = await B("pair", mac=BUDS["mac"])
        assert r["ok"] and r["kind"] == "audio" and r["sound"] == {"sink": sink, "default_by": "kairos"}, r
        assert json.loads((TMP / "pactl.json").read_text())["default"] == sink
        r = await B("connect", mac=BUDS["mac"])
        assert r["ok"] and r["sound"] == {"sink": sink, "default_by": "wireplumber"}, r
    finally:
        await hw.AGENT.stop()
    return f"sink {sink}: set by kairos when WirePlumber had not, reported as wireplumber when it had"


@test("airplane_mode", ["R78"])
async def t_airplane():
    """vibe.hw.airplane: set(true) = WiFi radio off (NetworkManager keeps it) + Bluetooth rfkill
    block (systemd-rfkill keeps it); get + vibe.hw.get() say so; set(false) brings both back"""
    nm_world([ap("OpenAP", 87, "WPA2", "pw-home-123", n=1)], [prof("OpenAP", "OpenAP", HOME, "pw-home-123")], active=HOME)
    bt_world([])
    g = await hw.handle({"device": "airplane"})
    assert g == {"available": True, "airplane": False, "wifi_enabled": True, "bluetooth_blocked": False}, g
    r = await hw.handle({"device": "airplane", "op": "set", "on": True})
    assert r["ok"] and r["airplane"] is True, r
    assert nm_state()["radio"] is False and (SYS / "class/rfkill/rfkill0/soft").read_text().strip() == "1"
    g = await hw.handle({"device": "airplane", "op": "get"})
    assert g["airplane"] and g["wifi_enabled"] is False and g["bluetooth_blocked"], g
    s = await hw.summary()
    assert s["airplane"] is True and s["wifi"]["enabled"] is False and s["bluetooth"]["blocked"], s["airplane"]
    r = await hw.handle({"device": "airplane", "op": "set", "on": False})
    assert r["ok"] and r["airplane"] is False, r
    g = await hw.handle({"device": "airplane"})
    assert not g["airplane"] and g["wifi_enabled"] and not g["bluetooth_blocked"], g
    assert (await W("status"))["connected"] and (await B("status"))["powered"]
    assert "airplane" in (await hw.handle({"device": "nope"}))["error"]
    return "on: radio off + bt blocked (get/summary agree); off: both back, WiFi re-joined"


# ----------------------------------------------------------------------------- static contracts
@test("static_server_read_write_split", ["R78", "R66"])
def t_server():
    """R66: the new READ ops stay open (saved, scan, airplane get), everything that changes the
    machine is guarded; the kernel wires the online() hooks; h_hw logs names/addresses only"""
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        raise SkipTest("aiohttp not installed")
    if not os.environ.get("VIBEOS_DIR"):
        d = tempfile.mkdtemp(prefix="kairos-wbs-")
        os.environ.update(VIBEOS_DIR=d, VIBEOS_MANIFEST=str(REPO / "manifests" / "tier-8gb.json"),
                          VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(Path(d) / "log"))
    import server
    hw.init(TMP)                     # server.py ran hw.init(VIBEOS_DIR): back to this test's mocks
    read = [("wifi", "status"), ("wifi", "scan"), ("wifi", "saved"), ("bluetooth", "status"), ("bluetooth", "scan"),
            ("airplane", ""), ("airplane", "get")]
    write = [("wifi", "connect"), ("wifi", "disconnect"), ("wifi", "forget"), ("wifi", "on"), ("wifi", "off"),
             ("wifi", "signin"), ("bluetooth", "on"), ("bluetooth", "off"), ("bluetooth", "pair"),
             ("bluetooth", "connect"), ("bluetooth", "disconnect"), ("bluetooth", "remove"), ("airplane", "set")]
    for dev, op in read:
        assert server.hw_op_is_read({"device": dev, "op": op}), (dev, op)
    for dev, op in write:
        assert not server.hw_op_is_read({"device": dev, "op": op}), (dev, op)
    src = (REPO / "kernel" / "server.py").read_text()
    assert "hw.ON_CHANGE.append(KB.reset_online)" in src and "KB.connectivity = hw.nm_connectivity" in src
    assert server.KB.reset_online in hw.ON_CHANGE and server.KB.connectivity is hw.nm_connectivity
    h = re.search(r"async def h_hw\(request\):.*?\n\n\n", src, re.S).group(0)
    assert "password" not in h.replace("a password is never logged", ""), "h_hw must never touch the password"
    return f"{len(read)} reads open, {len(write)} writes guarded; online() hooks wired; h_hw logs no password"


@test("static_bridge_and_host_pages", ["R78", "R12"])
def t_bridge():
    """the injected bridge has the new members (and stays an inert literal that parses); both host
    pages resolve WiFi/Bluetooth/airplane answers that carry ok + an error code instead of throwing"""
    js = (REPO / "shell" / "shell.js").read_text()
    host = (REPO / "shell" / "bench" / "host.js").read_text()
    b = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1)
    assert "${" not in b and "`" not in b and not re.search(r"\\(?!/script>)", b), "bridge literal must stay inert"
    for needle in ('saved: () => call("hw", { device: "wifi", op: "saved" })', 'signin: () => call("hw", { device: "wifi", op: "signin" })',
                   'on: () => call("hw", { device: "wifi", op: "on" })', 'off: () => call("hw", { device: "wifi", op: "off" })',
                   'op: "scan", fresh: o === true || !!(o && o.fresh)', 'airplane: {', 'device: "airplane", op: "get"',
                   'device: "airplane", op: "set"', 'op: "scan", seconds:'):
        assert needle in b, needle
    rule = 'if (r.error && !(typeof r.ok === "boolean" && ["wifi", "bluetooth", "airplane"].includes(q.device))) throw new Error(r.error);'
    for name, src in (("shell.js", js), ("host.js", host)):
        h = re.search(r"async function handleBridge\([^)]*\) \{.*?\n\}", src, re.S).group(0)
        part = h[h.index('method === "hw"'):h.index('method === "wiki"')]
        assert rule in part and "if (r.error) throw new Error(r.error);" not in part, name
    parsed = "static only"
    try:
        import jscheck
        if jscheck.enabled():
            # every <script> element of the bridge on its own (the main one + the late-picture block, SD run 2026-10-13)
            scripts = re.findall(r"<script\b[^>]*>(.*?)<\\/script>", b, re.S)
            assert len(scripts) >= 2, len(scripts)
            for label, src in [(f"BRIDGE_SRC script {i + 1}", s) for i, s in enumerate(scripts)] + [("shell.js", js), ("host.js", host)]:
                ok, msg, line, col = jscheck._run(src)
                assert ok, f"{label}: {msg} at {line}:{col}"
            parsed = f"parsed by {jscheck.CHECKER['kind']}"
    except ImportError:
        pass
    return f"bridge members + inert literal; ok+error answers resolve in shell.js and host.js; {parsed}"


def _dock_prompt():
    return re.search(r'data-app="settings" data-prompt="([^"]+)"', (REPO / "shell" / "index.html").read_text()).group(1)


@test("static_docs_for_the_coder_llm", ["R78", "R59", "R39", "R43"])
def t_docs():
    """the sheet + recipes a generated app gets carry the render-ready fields (bars, needs,
    connectivity, kind, code) and the flows (no password for OWE, Sign in for a portal, a
    keyboard's code); the dock Settings prompt covers Bluetooth + airplane; the dock prompt's
    doc pack still fits the 8 GB budget with room to spare"""
    net = (REPO / "docs" / "api" / "network.md").read_text()
    for n in ("vibe.hw.wifi.status()", "vibe.hw.wifi.scan(", "vibe.hw.wifi.connect(", "vibe.hw.wifi.disconnect(",
              "vibe.hw.wifi.forget(", "vibe.hw.wifi.saved()", "vibe.hw.wifi.signin()", "vibe.hw.wifi.on()",
              "bars", "needs", "connectivity", "portal", "wrong_password", "vibe.hw.bluetooth.scan(", "vibe.hw.bluetooth.pair(",
              "kind", "code", "enter_code", "trusted", "vibe.hw.airplane", "Flow for a WiFi app"):
        assert n in net, f"network.md lacks {n!r}"
    wifi = (REPO / "docs" / "wiki" / "wifi-manager.md").read_text()
    for n in ("needs", "bars", "OWE", "signin()", "portal", "wrong_password", "vibe.hw.wifi.connect"):
        assert n in wifi, f"wifi-manager.md lacks {n!r}"
    btw = (REPO / "docs" / "wiki" / "bluetooth-manager.md").read_text()
    assert btw.startswith("<!-- keywords:") and "bluetooth" in btw.split("-->")[0]
    for n in ("vibe.hw.bluetooth.scan(", "pair(", "enter_code", "code", "kind", "trusted", "sound", "connected"):
        assert n in btw, f"bluetooth-manager.md lacks {n!r}"
    api = (REPO / "docs" / "API.md").read_text()
    for n in ("vibe.hw.wifi.saved()", "vibe.hw.wifi.signin()", "vibe.hw.airplane.set(", "vibe.hw.bluetooth.pair(",
              "### WiFi connect — full reference", "### Bluetooth & airplane mode [R78]"):
        assert n in api, f"API.md lacks {n!r}"
    dock = _dock_prompt()
    for n in ("Bluetooth", "airplane", "signin", "code"):
        assert n in dock, f"the dock Settings prompt lacks {n!r}"
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return "docs carry fields + flows (budget check needs the server import: aiohttp missing)"
    if not os.environ.get("VIBEOS_DIR"):
        d = tempfile.mkdtemp(prefix="kairos-wbs-")
        os.environ.update(VIBEOS_DIR=d, VIBEOS_MANIFEST=str(REPO / "manifests" / "tier-8gb.json"),
                          VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(Path(d) / "log"))
    import server
    import prompts
    hw.init(TMP)
    api_docs, wiki = server.api_docs_for(dock), server.wiki_pages_for(dock)
    assert [n for n, _ in api_docs] == ["system-settings", "network"], [n for n, _ in api_docs]
    assert wiki[0][0] == "settings-app" and len(wiki) == 2 and wiki[1][0] in ("wifi-manager", "bluetooth-manager"), [n for n, _ in wiki]
    budget = server.prompt_char_budget(prompts.APP_SYSTEM)
    used = (len("Open this app: " + dock) + 2400 + 70 + len(prompts.API_HEADER) + sum(len(t) + 20 for _, t in api_docs)
            + len(prompts.WIKI_HEADER) + sum(len(t) + 20 for _, t in wiki) + 1700)
    assert used <= budget, f"dock prompt would overflow: {used} > {budget}"
    assert server.wiki_pages_for("a bluetooth manager to pair my headphones")[0][0] == "bluetooth-manager"
    assert server.wiki_pages_for("a wifi manager to connect to my router")[0][0] == "wifi-manager"
    assert server.api_docs_for("pair bluetooth headphones")[0][0] == "network"
    return f"fields + flows in network.md / wiki / API.md / dock prompt; dock pack {used} of {budget} chars ({budget - used} spare)"


@test("static_xinitrc_signin_window", ["R78", "R52"])
def t_xinit():
    """.xinitrc opens the sign-in page the kernel asks for in a real Chromium --app window (its own
    profile, never a kiosk) and removes the flag; the close flag ends that window — run
    hermetically with a fake chromium/pkill"""
    xi = (INC / "home" / "kairos" / ".xinitrc").read_text()
    subprocess.run(["sh", "-n", str(INC / "home" / "kairos" / ".xinitrc")], check=True)
    src = (REPO / "kernel" / "hw.py").read_text()
    assert re.search(r'KAIROS_OPEN_URL", "/run/kairos/open-url"', src) and re.search(r'KAIROS_CLOSE_URL", "/run/kairos/close-url"', src)
    assert "OPEN_URL=/run/kairos/open-url" in xi and "CLOSE_URL=/run/kairos/close-url" in xi
    fn = re.search(r"^signin_window\(\) \{\n.*?^\}\n", xi, re.M | re.S).group(0)
    loop = xi.split("while true; do\n        sleep 3", 1)[1].split("done", 1)[0]
    assert "signin_window" in loop, "the supervisor loop must check the flags every round"
    assert xi.count("chromium --kiosk ") == 2, "the sign-in window is not a kiosk"
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / "bin").mkdir()
        log, pids = t / "calls", t / "pids"
        (t / "bin" / "chromium").write_text(f'#!/bin/sh\necho "chromium $*" >> "{log}"\necho $$ >> "{pids}"\nexec sleep 30\n')
        (t / "bin" / "pkill").write_text(f'#!/bin/sh\necho "pkill $*" >> "{log}"\n')
        for f in (t / "bin").iterdir():
            f.chmod(0o755)
        code = fn.replace("/run/kairos/open-url", str(t / "open-url")).replace("/run/kairos/close-url", str(t / "close-url"))
        code = re.sub(r"OPEN_URL=\S+", f"OPEN_URL={t / 'open-url'}", code)
        code = re.sub(r"CLOSE_URL=\S+", f"CLOSE_URL={t / 'close-url'}", code)
        head = f"OPEN_URL={t / 'open-url'}\nCLOSE_URL={t / 'close-url'}\n"
        (t / "open-url").write_text("http://login.portal.test/?next=a b\n")
        env = dict(os.environ, PATH=f"{t / 'bin'}:/usr/bin:/bin")
        r = subprocess.run(["sh", "-c", head + code + "\nsignin_window\nsleep 0.3\nsignin_window\n"], env=env,
                           capture_output=True, text=True, timeout=20)
        assert r.returncode == 0, r.stderr
        calls_ = [c for c in log.read_text().splitlines() if c.startswith("chromium ")]
        assert len(calls_) == 1 and "--app=http://login.portal.test/?next=a b" in calls_[0] \
            and "--user-data-dir=/tmp/kairos-signin" in calls_[0] and "--kiosk" not in calls_[0], calls_
        assert not (t / "open-url").exists() and "xinitrc: sign-in window" in r.stdout, r.stdout
        (t / "close-url").write_text("close\n")
        r = subprocess.run(["sh", "-c", head + code + "\nsignin_window\n"], env=env, capture_output=True, text=True, timeout=20)
        assert "pkill -f user-data-dir=/tmp/kairos-signin" in log.read_text() and not (t / "close-url").exists(), log.read_text()
        (t / "open-url").write_text("javascript:alert(1)\n")
        r = subprocess.run(["sh", "-c", head + code + "\nsignin_window\n"], env=env, capture_output=True, text=True, timeout=20)
        assert log.read_text().count("chromium ") == 1 and not (t / "open-url").exists(), "only http(s) URLs open"
        for pid in (pids.read_text().split() if pids.exists() else []):
            try:
                os.kill(int(pid), 15)
            except (ProcessLookupError, ValueError):
                pass
    return "flag -> one chromium --app (own profile, not a kiosk), flag removed; close flag -> pkill; non-http refused"


@test("static_bootlog_wifi_bt_capture", ["R78", "R51"])
def t_bootlog():
    """the stick capture proves what the owner's run did: NM connectivity + the check conf, WiFi
    profiles WITH their SSIDs, the radio state, paired Bluetooth devices with trust flags, the rfkill
    state systemd-rfkill keeps, the default sink and the kernel's own WiFi/BT log lines"""
    bl = (INC / "usr" / "local" / "sbin" / "kairos-bootlog").read_text()
    subprocess.run(["sh", "-n", str(INC / "usr" / "local" / "sbin" / "kairos-bootlog")], check=True)
    sec = bl.split('echo "---------- network / WiFi ----------"', 1)[1].split('echo "---------- displays', 1)[0]
    for n in ("IP4-CONNECTIVITY", "networking connectivity", "NetworkManager/conf.d", "802-11-wireless.ssid",
              "radio", "---------- Bluetooth ----------", "devices Paired", "Trusted", "/var/lib/systemd/rfkill",
              "get-default-sink", "bt-agent"):
        assert n in sec, f"bootlog network/BT capture lacks {n!r}"
    assert "psk" not in sec and "--show-secrets" not in sec and not re.search(r"nmcli[^\n]* -s ", sec), "never capture a password"
    return "connectivity + conf, profiles with SSIDs, radio, paired devices + trust, rfkill state, default sink, kernel lines"


@test("static_requirement_and_doc", ["R78"])
def t_req():
    req = (REPO / "docs" / "REQUIREMENTS.md").read_text()
    m = re.search(r"\*\*R78 — [^*]+\*\*(.*?)(?=\n- \*\*R\d+ — |\Z)", req, re.S)
    assert m, "R78 missing from REQUIREMENTS.md"
    body = m.group(1)
    for n in ("nothing prebaked", "vibe.hw.airplane", "OWE", "wrong_password", "trust", "rfkill", "signin", "tests/test_wifi_bt.py"):
        assert n in body, f"R78 lacks {n!r}"
    doc = (REPO / "docs" / "WIFI-BT-2026-10-07.md").read_text()
    for n in ("## API", "## VM proof", "mac80211_hwsim", "hostapd", "## Hotfix"):
        assert n in doc, f"docs/WIFI-BT-2026-10-07.md lacks {n!r}"
    return "R78 in REQUIREMENTS.md (scope + tests), the log with API / VM proof / hotfix sections"


def main():
    tests = [v for v in globals().values() if getattr(v, "_test", False)]
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    if only:
        tests = [t for t in tests if any(o in t._name for o in only)]
    print(f"kAIrOS WiFi & Bluetooth tests [R78] — {len(tests)} tests", flush=True)
    try:
        for t in tests:
            t()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    if not only:
        out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
        (REPO / "tests" / "results-wifi-bt.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-wifi-bt.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
