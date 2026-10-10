"""kAIrOS hardware manager — the easy laptop-setup API [R38].

Everything a fresh laptop needs to be configured from inside the OS — WiFi,
Bluetooth, airplane mode [R78], battery, screen brightness, volume, power — exposed as ONE simple endpoint
(POST /api/hw {"device", "op", ...}) that generated apps reach via vibe.hw.*.

Design rules:
  * every operation degrades gracefully: missing hardware or tools yield
    {"available": false, "reason": …} instead of errors, so the same app works
    identically in a VM, in the dev container and on bare metal;
  * tools are resolved from $VIBEOS_DIR/bin first (lets the test suite drop in
    mock binaries), then $PATH;
  * sysfs root is overridable via $VIBEOS_SYSFS (test fixtures).
"""
import asyncio
import os
import re
import shutil
import time
from pathlib import Path

SYSFS = Path(os.environ.get("VIBEOS_SYSFS", "/sys"))
_BIN = None  # set by init()
# [R78] called (no arguments) after every radio/network write op — the kernel resets
# knowledge.online()'s cache here, so web search follows a WiFi connect at once
ON_CHANGE = []


def init(vibeos_dir: Path):
    global _BIN
    _BIN = Path(vibeos_dir) / "bin"


def _tool(name: str):
    if _BIN and (_BIN / name).exists() and os.access(_BIN / name, os.X_OK):
        return str(_BIN / name)
    return shutil.which(name)


async def _run(args, timeout=25, env=None):
    """Run a tool, return (rc, stdout). rc=-1 when the tool is missing/failed.
    A child that outlives `timeout` is killed and reaped: wait_for only cancels
    the await, so without this every slow nmcli/bluetoothctl/xrandr call
    leaked an orphan (each holding a NetworkManager/bluez D-Bus request)."""
    if not args or args[0] is None:
        return -1, ""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, out.decode("utf-8", "ignore")
    except asyncio.TimeoutError:
        await _reap(proc)
        return -1, f"{os.path.basename(str(args[0]))} timed out after {timeout}s"
    except Exception as e:
        await _reap(proc)
        return -1, str(e)


async def _reap(proc):
    """Kill + wait a child so a timed-out/failed call never leaves a zombie."""
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception:
        pass


def _read(path: Path):
    try:
        return path.read_text().strip()
    except Exception:
        return None


# ---------------------------------------------------------------- radio-state cache [R78]
# Generated apps poll status()/vibe.hw.get() every few seconds; without a cache every poll
# spawned nmcli + bluetoothctl several times. Values live CACHE_TTL seconds (a missing radio /
# a silent daemon 10 s, saved profiles 30 s); every write op clears everything (invalidate()).
CACHE_TTL = 2.0
PROFILES_TTL = 30.0
_CACHE = {}          # key -> (expiry monotonic, value)
_LOCKS = {}


def _cached(key):
    hit = _CACHE.get(key)
    return hit[1] if hit and time.monotonic() < hit[0] else None


async def _memo(key, fn, ttl=CACHE_TTL):
    """fn()'s result shared by every caller within ttl seconds — one spawn per window."""
    hit = _cached(key)
    if hit is not None:
        return hit
    # a lock belongs to one event loop (the test suite runs several loops in one process; a
    # loop id can be reused after a loop is gone, so the loop itself is compared)
    loop = asyncio.get_running_loop()
    held = _LOCKS.get(key)
    if held is None or held[0] is not loop:
        held = _LOCKS[key] = (loop, asyncio.Lock())
    async with held[1]:
        hit = _cached(key)
        if hit is not None:
            return hit
        val = await fn()
        hold = ttl if not (isinstance(val, dict) and val.get("available") is False) else max(ttl, 10.0)
        _CACHE[key] = (time.monotonic() + hold, val)
        return val


def invalidate():
    """Forget every cached radio state and tell the listeners (knowledge.online's cache) [R78]."""
    _CACHE.clear()
    for cb in list(ON_CHANGE):
        try:
            cb()
        except Exception:
            pass


def _changed(result):
    invalidate()
    return result


def _rfkill(kind: str):
    """(present, soft-blocked, hard-blocked) for the radios of one rfkill type (bluetooth | wlan)"""
    found = soft = hard = False
    for d in sorted(SYSFS.glob("class/rfkill/rfkill*")):
        if _read(d / "type") == kind:
            found = True
            soft = soft or _read(d / "soft") == "1"
            hard = hard or _read(d / "hard") == "1"
    return found, soft, hard


# ---------------------------------------------------------------- wifi (NetworkManager) [R38, R78]
# nmcli terse output (-t) escapes ':' and '\' inside values (-e yes, the default): an SSID
# "Cafe:Bar" arrives as "Cafe\:Bar" and BSSIDs always carry "\:" — split on the UNESCAPED ':'
# only. status().ssid is the network's real SSID (802-11-wireless.ssid of the active profile),
# never the NetworkManager profile NAME (profile "ffm.freifunk.net" joined to the OWE twin
# "owe.ffm.freifunk.net"; old profiles may be named "X 1"), so disconnect/forget by a scanned
# SSID work. needs = what joining takes: OWE / OWE-TM ("Enhanced Open", Freifunk) are
# encrypted WITHOUT a password.
NMT = ["-t", "-e", "yes"]
DEV_FIELDS = "DEVICE,TYPE,STATE,IP4-CONNECTIVITY,CONNECTION,CON-UUID,DBUS-PATH"
CONNECTIVITY = ("full", "portal", "limited", "none", "unknown")
WIFI_ERRORS = ("wrong_password", "not_in_range", "timeout", "needs_password", "unsupported",
               "not_connected", "not_saved", "ssid_required", "failed")
_SCAN = {"t": None}          # monotonic time of this kernel's last forced rescan (age_s fallback)
_JOIN = {"gen": 0}           # bumped by every connect(): a late restore never undoes a newer join


def _nm_split(line: str) -> list:
    """one `nmcli -t -e yes` line -> its fields, escapes undone (`\\:` -> ':', `\\\\` -> '\\')"""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if esc:
        cur.append("\\")
    out.append("".join(cur))
    return out


def _nm_rows(out: str, n: int) -> list:
    """terse table rows with at least n fields (warnings / error lines have fewer)"""
    return [p for p in (_nm_split(l) for l in (out or "").splitlines() if l) if len(p) >= n]


def _nm_blocks(out: str) -> list:
    """`nmcli -t -f a,b con show uuid X uuid Y` -> [{a:…, b:…}, …], one dict per profile"""
    blocks, cur = [], {}
    for line in (out or "").splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        p = _nm_split(line)
        if len(p) >= 2:
            if p[0] in cur:
                blocks.append(cur)
                cur = {}
            cur[p[0]] = ":".join(p[1:])
    if cur:
        blocks.append(cur)
    return blocks


_ESC = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")


def _nm_detail(out: str, secret: str = "") -> str:
    """nmcli's answer for `detail`: terminal escapes and progress redraws ("\x1b[2K\r") removed,
    the last "Error:" line when there is one (nmcli prints warnings before it), never the password"""
    lines = [l.strip() for l in _ESC.sub("", out or "").splitlines() if l.strip()]
    err = [l for l in lines if l.startswith("Error:")]
    text = (err[-1] if err else (lines[-1] if lines else ""))[-300:]
    return text.replace(secret, "•••") if secret else text


def _nm_reason(out: str) -> str:
    o = (out or "").strip()
    if "not running" in o:
        return "NetworkManager is not running"
    return (o.splitlines() or ["nmcli failed"])[-1][:160]


def _bars(signal) -> int:
    """0-4 like NetworkManager's own BARS column"""
    s = int(signal or 0)
    return 4 if s > 80 else 3 if s > 55 else 2 if s > 30 else 1 if s > 5 else 0


def _mhz(freq):
    m = re.search(r"\d+", str(freq or ""))
    return int(m.group()) if m else None


def _band(freq):
    f = _mhz(freq)
    if f is None:
        return None
    return "6" if f >= 5925 else "5" if f >= 4900 else "2.4"


def wifi_needs(security: str) -> str:
    """what joining a network takes, from nmcli's SECURITY: none (open, OWE, OWE-TM),
    password (WPA1/2/3 personal, SAE, WEP), login (802.1X / EAP: user + certificate), or
    unsupported (anything else)"""
    s = (security or "").upper().strip()
    if s in ("", "--", "OPEN", "NONE"):
        return "none"
    if "802.1X" in s or re.search(r"\bEAP\b", s) or "ENTERPRISE" in s:
        return "login"
    if re.search(r"WPA|SAE|WEP|PSK", s):
        return "password"
    if "OWE" in s:
        return "none"
    return "unsupported"


def _up(state: str) -> bool:
    return state == "connected" or state.startswith("connected ")


def _norm_conn(value: str) -> str:
    v = (value or "").strip().lower()
    return next((c for c in CONNECTIVITY if v == c or v.endswith(f"({c})")), "unknown")


def _nm_error(out: str, had_password: bool) -> str:
    """map an nmcli failure onto the documented error codes (docs/api/network.md)"""
    o = (out or "").lower()
    if ("secrets were required" in o or "no-secrets" in o or "no secrets" in o or "4-way" in o
            or "802-11-wireless-security.psk" in o or "wrong password" in o):
        return "wrong_password" if had_password else "needs_password"
    if "no network with ssid" in o or "could not be found" in o or "not found" in o:
        return "not_in_range"
    if "timeout" in o or "timed out" in o:
        return "timeout"
    if "not supported" in o or "unsupported" in o or "802.1x" in o or "802-1x" in o or re.search(r"\beap\b", o):
        return "unsupported"
    return "failed"


async def wifi(op, body):
    nm = _tool("nmcli")
    if not nm:
        return {"available": False, "reason": "NetworkManager (nmcli) not installed"}
    if op == "status":
        return await _memo("wifi", lambda: _wifi_status(nm))
    if op == "scan":
        return await _wifi_scan(nm, bool(body.get("fresh")))
    if op in ("disconnect", "forget", "on", "off"):
        _JOIN["gen"] += 1                      # the user's newer word: no pending restore may undo it
    if op == "connect":
        return _changed(await _wifi_connect(nm, body))
    if op == "disconnect":
        return _changed(await _wifi_disconnect(nm))
    if op == "forget":
        return _changed(await _wifi_forget(nm, body.get("ssid") or ""))
    if op == "saved":
        return await _wifi_saved(nm)
    if op in ("on", "off"):
        rc, out = await _run([nm, "radio", "wifi", op])
        return _changed({"ok": rc == 0, "error": None if rc == 0 else "failed",
                         "enabled": (op == "on") if rc == 0 else None, "detail": _nm_detail(out)})
    if op == "signin":
        return await _wifi_signin(nm)
    return {"error": f"unknown wifi op '{op}' — status|scan|connect|disconnect|saved|forget|on|off|signin"}


async def _nm_devices(nm):
    """(the WiFi devices [{device, state, connectivity, connection, uuid, path}], "") or (None, reason)"""
    rc, out = await _run([nm, *NMT, "-f", DEV_FIELDS, "dev", "status"], timeout=8)
    if rc != 0 and "field" in out.lower():     # an older nmcli without IP4-CONNECTIVITY / CON-UUID
        rc, out = await _run([nm, *NMT, "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev", "status"], timeout=8)
        rows = [[p[0], p[1], p[2], "unknown", p[3], "", ""] for p in _nm_rows(out, 4)]
    else:
        rows = _nm_rows(out, 7)
    if rc != 0:
        return None, _nm_reason(out)
    return [{"device": p[0], "state": p[2], "connectivity": _norm_conn(p[3]),
             "connection": p[4] or None, "uuid": p[5] or None, "path": p[6] or None}
            for p in rows if p[1] == "wifi"], ""


async def _wifi_profiles(nm, fresh=False):
    """every saved WiFi profile: [{name, uuid, ssid, auto, last_used}] (cached 30 s; writes clear it)"""
    async def load():
        rc, out = await _run([nm, *NMT, "-f", "NAME,UUID,TYPE,AUTOCONNECT,TIMESTAMP", "con", "show"], timeout=8)
        rows = [p for p in _nm_rows(out, 5) if p[2] in ("802-11-wireless", "wifi")]
        ssids = {}
        if rows:
            args = [a for p in rows for a in ("uuid", p[1])]
            rc2, out2 = await _run([nm, *NMT, "-f", "connection.uuid,802-11-wireless.ssid", "con", "show", *args],
                                   timeout=8)
            for b in _nm_blocks(out2):
                if b.get("connection.uuid"):
                    ssids[b["connection.uuid"]] = b.get("802-11-wireless.ssid", "")
        return [{"name": p[0], "uuid": p[1], "ssid": ssids.get(p[1]) or p[0], "auto": p[3] == "yes",
                 "last_used": int(p[4]) if p[4].isdigit() and int(p[4]) > 0 else None} for p in rows]
    if fresh:
        _CACHE.pop("wifi-profiles", None)
    return await _memo("wifi-profiles", load, PROFILES_TTL)


async def _wifi_aps(nm, rescan="no", ifname=None):
    """every BSS NetworkManager knows: [{in_use, bssid, ssid, chan, freq, signal, security}]"""
    args = [nm, *NMT, "-f", "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY", "dev", "wifi", "list",
            "--rescan", rescan]
    if ifname:
        args += ["ifname", ifname]
    rc, out = await _run(args, timeout=45 if rescan == "yes" else 10)
    if rescan == "yes":
        _SCAN["t"] = time.monotonic()
    return [{"in_use": "*" in p[0], "bssid": p[1], "ssid": p[2], "chan": p[3], "freq": p[4],
             "signal": int(p[5]) if p[5].isdigit() else 0, "security": p[6]} for p in _nm_rows(out, 7)]


def _networks(aps, saved_ssids, active_ssid):
    """one entry per SSID (its strongest BSS), hidden BSSs dropped, render-ready: bars, band,
    needs, saved, in_use; sorted by signal. OWE transition mode (Freifunk): an open SSID marked
    "OWE-TM" + a hidden OWE BSS on the same channel. NetworkManager 1.52 lists that hidden BSS
    several times (no SSID — the in-use row once joined —, the transition SSID, its own learned
    name "owe.…"), all with SECURITY "OWE": every such row is folded into the OWE-TM network
    (VM proof, docs/WIFI-BT-2026-10-07.md)."""
    tm = {}
    for ap in aps:
        if ap["ssid"] and "OWE-TM" in (ap["security"] or "").upper():
            tm.setdefault(_mhz(ap["freq"]), ap["ssid"])
    names = {a["ssid"] for a in aps}
    best, used = {}, set()
    for ap in aps:
        s = ap["ssid"]
        sec = (ap["security"] or "").upper().strip()
        twin = tm.get(_mhz(ap["freq"])) if sec == "OWE" else None
        if twin is None and (ap["in_use"] and active_ssid and s and s != active_ssid and active_ssid in names
                             and sec == "OWE"):
            twin = active_ssid                          # a twin on another channel: the joined network
        if twin is not None:
            if ap["in_use"]:
                used.add(twin)
            continue
        if not s or s == "--":
            continue
        if ap["in_use"]:
            used.add(s)
        if s not in best or ap["signal"] > best[s]["signal"]:
            best[s] = ap
    nets = [{"ssid": s, "signal": ap["signal"], "bars": _bars(ap["signal"]), "band": _band(ap["freq"]),
             "security": ap["security"] if ap["security"] not in ("", "--") else "open",
             "needs": wifi_needs(ap["security"]), "saved": s in saved_ssids,
             "in_use": s in used or s == active_ssid} for s, ap in best.items()]
    nets.sort(key=lambda n: -n["signal"])
    return nets


async def _wifi_status(nm):
    devs, why = await _nm_devices(nm)
    base = {"connected": False, "ssid": None, "signal": None, "bars": 0, "ip": None, "connectivity": "none"}
    if devs is None:
        return {"available": False, "reason": why, "enabled": False, **base, "devices": []}
    public = [{"device": d["device"], "state": d["state"], "connection": d["connection"]} for d in devs]
    if not devs:
        return {"available": False, "reason": "no WiFi adapter found", "enabled": False, **base, "devices": public}
    enabled = True
    present, soft, hard = _rfkill("wlan")
    if hard:
        enabled = False                         # the laptop's hardware switch / Fn key: no API can undo it
    elif any(d["state"] == "unavailable" for d in devs):
        # radio off — or wpa_supplicant still starting (NM: "Couldn't initialize supplicant
        # interface: Timeout", ~10 s at every boot on the AX210): only the radio tells them apart
        rc, out = await _run([nm, "-t", "-f", "WIFI", "radio"], timeout=6)
        enabled = (out.strip().splitlines() or [""])[-1] != "disabled" if rc == 0 else True
    res = {"available": True, "enabled": enabled, **base, "devices": public}
    if hard:
        res["reason"] = "WiFi is switched off by the hardware switch (or Fn key)"
    con = next((d for d in devs if _up(d["state"])), None)
    if con:
        prof = next((p for p in await _wifi_profiles(nm) if p["uuid"] == con["uuid"]), None)
        if prof is None and con["uuid"]:                   # a profile made since the cache was filled
            prof = next((p for p in await _wifi_profiles(nm, fresh=True) if p["uuid"] == con["uuid"]), None)
        ap = next((a for a in await _wifi_aps(nm, "no", con["device"]) if a["in_use"]), None)
        rc, out = await _run([nm, *NMT, "-f", "IP4.ADDRESS", "dev", "show", con["device"]], timeout=6)
        ip = next((p[1].split("/")[0] for p in _nm_rows(out, 2) if p[0].startswith("IP4.ADDRESS") and p[1]), None)
        sig = ap["signal"] if ap else None
        res.update(connected=True, ssid=prof["ssid"] if prof else con["connection"], signal=sig,
                   bars=_bars(sig), ip=ip, connectivity=con["connectivity"], device=con["device"])
    elif enabled and any(d["state"].startswith(("connecting", "prepare", "config", "need-auth", "ip-config"))
                         for d in devs):
        res["connecting"] = True                            # NM is joining (autoconnect at boot, a connect())
    elif enabled and all(d["state"] == "unavailable" for d in devs):
        res["starting"] = True                              # the supplicant window at boot: answer, don't fail
    return res


async def _scan_age(dev_path):
    """seconds since NetworkManager's last finished scan (Device.Wireless LastScan, CLOCK_BOOTTIME
    ms) — else since this kernel's last forced rescan — else None"""
    bctl = _tool("busctl")
    if bctl and dev_path:
        rc, out = await _run([bctl, "get-property", "org.freedesktop.NetworkManager", dev_path,
                              "org.freedesktop.NetworkManager.Device.Wireless", "LastScan"], timeout=4)
        m = re.match(r"x (-?\d+)", out.strip())
        if rc == 0 and m and int(m.group(1)) > 0:
            return max(0, round(time.clock_gettime(time.CLOCK_BOOTTIME) - int(m.group(1)) / 1000))
    return round(time.monotonic() - _SCAN["t"]) if _SCAN["t"] else None


async def _wifi_scan(nm, fresh):
    """the cached list at once (NetworkManager scans by itself); fresh=True = --rescan yes"""
    st = await _memo("wifi", lambda: _wifi_status(nm))
    if not st["available"]:
        return {"available": False, "reason": st.get("reason"), "networks": []}
    if not st["enabled"]:
        return {"available": True, "enabled": False, "networks": [], "age_s": None,
                "reason": st.get("reason") or "WiFi is switched off — vibe.hw.wifi.on()"}
    if st.get("starting"):
        return {"available": True, "enabled": True, "starting": True, "networks": [], "age_s": None}
    aps = await _wifi_aps(nm, "yes" if fresh else "no")
    if not fresh and not any(a["ssid"] for a in aps):      # nothing cached yet (radio just on): scan now
        aps = await _wifi_aps(nm, "yes")
    if fresh:
        _CACHE.pop("wifi", None)
    saved = {p["ssid"] for p in await _wifi_profiles(nm)}
    devs, _ = await _nm_devices(nm)
    path = next((d["path"] for d in devs or [] if d["state"] not in ("unavailable", "unmanaged")), None)
    return {"available": True, "enabled": True, "networks": _networks(aps, saved, st.get("ssid")),
            "age_s": await _scan_age(path)}


async def _await_connectivity(nm, dev, limit=6.0):
    """right after a join NM's per-device check is still running ('unknown') — wait a moment"""
    t0, conn = time.monotonic(), "unknown"
    while time.monotonic() - t0 < limit:
        devs, _ = await _nm_devices(nm)
        d = next((x for x in devs or [] if x["device"] == dev or dev is None), None)
        conn = d["connectivity"] if d and _up(d["state"]) else "unknown"
        if conn != "unknown":
            break
        await asyncio.sleep(0.5)
    return conn


async def _wifi_connect(nm, body):
    """join a network: a saved profile for this SSID is reused (a new password updates it and is
    rolled back when wrong), else a profile is made — and deleted again when the join fails, so a
    wrong password never leaves "X", "X 1", "X 2" behind. The password is never logged."""
    ssid, password = body.get("ssid") or "", body.get("password") or ""
    fail = {"ok": False, "ssid": ssid or None, "connectivity": None}
    if not isinstance(ssid, str) or not ssid:
        return {**fail, "ssid": None, "error": "ssid_required",
                "detail": "pass the network name: vibe.hw.wifi.connect(ssid, password)"}
    if not isinstance(password, str):
        password = str(password)
    _JOIN["gen"] += 1
    gen = _JOIN["gen"]

    def done(rc, out, conn=None):
        detail = _nm_detail(out, password)
        if rc == 0:
            return {"ok": True, "error": None, "detail": detail, "ssid": ssid, "connectivity": conn}
        return {**fail, "error": _nm_error(out, bool(password)), "detail": detail}

    st = await _wifi_status(nm)
    for _ in range(12):                                     # the supplicant's first seconds after boot
        if not st.get("starting"):
            break
        await asyncio.sleep(1)
        st = await _wifi_status(nm)
    if st.get("starting"):
        return {**fail, "error": "timeout", "detail": "the WiFi adapter is still starting — try again in a few seconds"}
    if not st["available"]:
        return {**fail, "error": "unsupported", "detail": st.get("reason")}
    if not st["enabled"]:
        if st.get("reason"):                                 # hardware switch: nothing to do
            return {**fail, "error": "failed", "detail": st["reason"]}
        await _run([nm, "radio", "wifi", "on"])
        await asyncio.sleep(3)
        st = await _wifi_status(nm)
    if st["connected"] and st["ssid"] == ssid and not password:
        return {"ok": True, "error": None, "detail": "already connected", "ssid": ssid,
                "connectivity": st["connectivity"]}
    devs, _ = await _nm_devices(nm)
    dev = st.get("device") or next((d["device"] for d in devs or [] if d["state"] != "unmanaged"), None)
    prev = next((d["uuid"] for d in devs or [] if d["device"] == dev and _up(d["state"])), None)
    profiles = sorted((p for p in await _wifi_profiles(nm, fresh=True) if p["ssid"] == ssid),
                      key=lambda p: -(p["last_used"] or 0))
    aps = [a for a in await _wifi_aps(nm, "no") if a["ssid"] == ssid]
    if not aps:
        aps = [a for a in await _wifi_aps(nm, "yes") if a["ssid"] == ssid]
    ap = max(aps, key=lambda a: a["signal"]) if aps else None
    if ap is None and not profiles:
        return {**fail, "error": "not_in_range", "detail": f"no network '{ssid}' in range"}
    needs = wifi_needs(ap["security"]) if ap else None
    if needs in ("login", "unsupported") and not profiles:
        return {**fail, "error": "unsupported",
                "detail": f"'{ssid}' needs a {'user/certificate login (802.1X)' if needs == 'login' else 'security mode'} this API cannot set up"}
    if needs == "password" and not password and not profiles:
        return {**fail, "error": "needs_password", "detail": f"'{ssid}' is secured — ask for the password"}
    ifname = ["ifname", dev] if dev else []
    prof = profiles[0] if profiles else None
    if prof is not None:
        restore = None
        if password and needs in ("password", None):
            rc, out = await _run([nm, "-s", *NMT, "-f", "802-11-wireless-security.key-mgmt,"
                                  "802-11-wireless-security.psk", "con", "show", "uuid", prof["uuid"]], timeout=8)
            kv = (_nm_blocks(out) or [{}])[0]
            if kv.get("802-11-wireless-security.key-mgmt") in ("wpa-psk", "sae"):
                restore = kv.get("802-11-wireless-security.psk") or None     # an agent-owned psk: nothing to restore
                rc, out = await _run([nm, "con", "modify", "uuid", prof["uuid"],
                                      "802-11-wireless-security.psk", password], timeout=10)
                if rc != 0:
                    return done(rc, out)
            elif needs == "password":
                prof = None            # an open/OWE/802.1X profile cannot take a password: make a new one
        if prof is not None:
            rc, out = await _run([nm, "--wait", "45", "con", "up", "uuid", prof["uuid"], *ifname], timeout=60)
            if rc != 0 and restore:                 # the old (working) password stays saved
                await _run([nm, "con", "modify", "uuid", prof["uuid"], "802-11-wireless-security.psk", restore],
                           timeout=10)
            if rc != 0 and prev:
                asyncio.ensure_future(_restore_previous(nm, dev, prev, gen))
            return done(rc, out, await _await_connectivity(nm, dev) if rc == 0 else None)
    before = {p["uuid"] for p in await _wifi_profiles(nm, fresh=True)}
    args = [nm, "--wait", "45", "dev", "wifi", "connect", ssid]
    if password and needs != "none":
        args += ["password", password]
    rc, out = await _run(args + ifname, timeout=60)
    if rc != 0:      # a failed join must not leave a profile behind (NM keeps it: "X", then "X 1", …)
        stale = [p["uuid"] for p in await _wifi_profiles(nm, fresh=True)
                 if p["uuid"] not in before and (p["ssid"] == ssid or p["name"].startswith(ssid))]
        if stale:
            await _run([nm, "con", "delete", *[a for u in stale for a in ("uuid", u)]], timeout=15)
            print(f"[kernel] wifi connect '{ssid}': removed {len(stale)} profile(s) the failed join left", flush=True)
        if prev:
            asyncio.ensure_future(_restore_previous(nm, dev, prev, gen))
    return done(rc, out, await _await_connectivity(nm, dev) if rc == 0 else None)


RESTORE_DELAY = 4.0


async def _restore_previous(nm, dev, prev_uuid, gen):
    """a failed join took the device off the network it was on: NetworkManager's autoconnect
    normally brings that one back by itself — if it has not within RESTORE_DELAY seconds (and no
    newer connect() started meanwhile), kAIrOS re-activates it"""
    await asyncio.sleep(RESTORE_DELAY)
    if _JOIN["gen"] != gen:
        return
    devs, _ = await _nm_devices(nm)
    d = next((x for x in devs or [] if x["device"] == dev), None)
    if d is None or _up(d["state"]) or d["state"].startswith("connecting"):
        print(f"[kernel] wifi: after the failed join {dev} is {d['state'] if d else 'gone'}"
              f"{' (NetworkManager re-joined by itself)' if d and d['uuid'] == prev_uuid else ''}", flush=True)
        invalidate()
        return
    rc, out = await _run([nm, "--wait", "30", "con", "up", "uuid", prev_uuid, "ifname", dev], timeout=40)
    print(f"[kernel] wifi: re-joined the previous network after a failed join: {'ok' if rc == 0 else 'failed'}", flush=True)
    invalidate()


async def _wifi_disconnect(nm):
    """leave the network the WiFi device is on (the old ssid argument is ignored: the device's
    active connection is what the user sees). NM does not auto-rejoin until the next connect()
    or reboot; the profile stays saved."""
    devs, why = await _nm_devices(nm)
    if devs is None:
        return {"ok": False, "error": "unsupported", "detail": why}
    up = [d["device"] for d in devs if _up(d["state"]) or d["state"].startswith("connecting")]
    if not up:
        return {"ok": True, "error": None, "detail": "not connected"}
    rc, out = await _run([nm, "dev", "disconnect", *up], timeout=20)
    return {"ok": rc == 0, "error": None if rc == 0 else "failed", "detail": _nm_detail(out)}


async def _wifi_forget(nm, ssid):
    """delete EVERY saved profile of this SSID (a profile NAME is accepted too: older apps passed
    status().ssid, which used to be the name)"""
    if not ssid:
        return {"ok": False, "error": "ssid_required", "deleted": 0, "detail": "pass the network name"}
    profiles = await _wifi_profiles(nm, fresh=True)
    hit = [p for p in profiles if p["ssid"] == ssid] or [p for p in profiles if p["name"] == ssid]
    if not hit:
        return {"ok": False, "error": "not_saved", "deleted": 0, "detail": f"no saved network '{ssid}'"}
    rc, out = await _run([nm, "con", "delete", *[a for p in hit for a in ("uuid", p["uuid"])]], timeout=20)
    return {"ok": rc == 0, "error": None if rc == 0 else "failed", "deleted": len(hit) if rc == 0 else 0,
            "detail": _nm_detail(out)}


async def _wifi_saved(nm):
    """the saved networks, one entry per SSID, most recently used first"""
    by = {}
    for p in await _wifi_profiles(nm):
        cur = by.setdefault(p["ssid"], {"ssid": p["ssid"], "auto": False, "last_used": None})
        cur["auto"] = cur["auto"] or p["auto"]
        if p["last_used"] and (cur["last_used"] or 0) < p["last_used"]:
            cur["last_used"] = p["last_used"]
    return {"available": True, "networks": sorted(by.values(), key=lambda n: -(n["last_used"] or 0))}


# ---------------------------------------------------------------- captive portal sign-in [R78]
# NetworkManager reports a portal per device (IP4-CONNECTIVITY "portal") once a connectivity
# check is configured (iso: etc/NetworkManager/conf.d/20-kairos-connectivity.conf — Debian
# ships none). signin() asks the kiosk supervisor (.xinitrc, user kairos) to open the portal in
# a real Chromium --app window: the kernel writes the URL into OPEN_URL, .xinitrc opens it and
# removes the flag (same ownership rules as KIOSK_RELOAD); once the device is online (or off
# the network, or after SIGNIN_LIMIT) the kernel writes CLOSE_URL and .xinitrc closes the window.
OPEN_URL = Path(os.environ.get("KAIROS_OPEN_URL", "/run/kairos/open-url"))
CLOSE_URL = Path(os.environ.get("KAIROS_CLOSE_URL", "/run/kairos/close-url"))
PORTAL_FALLBACK = "http://neverssl.com"
NM_CONF_DIRS = [Path(p) for p in os.environ.get(
    "KAIROS_NM_CONF_DIRS", "/usr/lib/NetworkManager/conf.d:/run/NetworkManager/conf.d:/etc/NetworkManager/conf.d"
).split(":") if p]
SIGNIN_POLL = float(os.environ.get("KAIROS_SIGNIN_POLL", "4"))
SIGNIN_LIMIT = float(os.environ.get("KAIROS_SIGNIN_LIMIT", "900"))
_SIGNIN = {"task": None}


def _check_config():
    """(uri, response) of NetworkManager's connectivity check, read like NetworkManager does:
    conf.d files from /usr/lib, /run, /etc (a later dir's file masks one of the same name) in
    the order of their names, a later value wins; (None, default) when no check is configured"""
    files = {}
    for d in NM_CONF_DIRS:
        for f in (d.glob("*.conf") if d.is_dir() else []):
            files[f.name] = f
    uri = resp = None
    enabled = True
    for name in sorted(files):
        sect = ""
        for line in (_read(files[name]) or "").splitlines():
            line = line.strip()
            if line.startswith("["):
                sect = line
            elif sect == "[connectivity]" and "=" in line and not line.startswith(("#", ";")):
                k, v = (x.strip() for x in line.split("=", 1))
                if k == "uri":
                    uri = v or None
                elif k == "response":
                    resp = v
                elif k == "enabled":
                    enabled = v.lower() not in ("false", "no", "0")
    uri = os.environ.get("KAIROS_CONNECTIVITY_URI") or (uri if enabled else None)
    return uri, resp or "NetworkManager is online"


async def _http_probe(url, dev=None, timeout=5.0):
    """(status, Location, start of the body) of `GET url` WITHOUT following redirects, sent over
    the WiFi device itself (SO_BINDTODEVICE, the kernel is root): a portal intercepts that
    device, not the default route (ethernet in the test VM, a second uplink on a desk). http://
    only (a portal can only answer plain HTTP); None when nothing answered."""
    import socket
    from urllib.parse import urlsplit
    u = urlsplit(url)
    if u.scheme != "http" or not u.hostname:
        return None
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(u.hostname, u.port or 80, type=socket.SOCK_STREAM), timeout)
    except Exception:
        return None
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    for family, stype, proto, _, addr in sorted(infos, key=lambda i: i[0] != socket.AF_INET):
        sock = socket.socket(family, stype, proto)
        sock.setblocking(False)
        try:
            if dev:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
                except (OSError, AttributeError):
                    pass                                # not root (tests) / no such device: default route
            await asyncio.wait_for(loop.sock_connect(sock, addr), timeout)
            await loop.sock_sendall(sock, (f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nUser-Agent: kAIrOS sign-in\r\n"
                                           "Connection: close\r\n\r\n").encode())
            data = b""
            while len(data) < 8192:
                chunk = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout)
                if not chunk:
                    break
                data += chunk
                if b"\r\n\r\n" in data and len(data.split(b"\r\n\r\n", 1)[1]) >= 64:
                    break
            head, _, body = data.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            status = int(lines[0].split()[1])
            loc = next((l.split(":", 1)[1].strip() for l in lines[1:] if l.lower().startswith("location:")), "")
            return status, loc, body[:512].decode("utf-8", "ignore")
        except Exception:
            continue
        finally:
            sock.close()
    return None


async def _portal_url(dev=None):
    """(url to open, portal?) — the check URI fetched over the device WITHOUT following
    redirects: a portal's 302 Location is the login page; an intercepted 200 means the check page
    itself shows it; the real answer = no portal on this route (neverssl: a page a portal would
    intercept)"""
    uri, expect = _check_config()
    if not uri:
        return PORTAL_FALLBACK, None
    got = await _http_probe(uri, dev)
    if got is None:
        return PORTAL_FALLBACK, None
    status, loc, body = got
    if 300 <= status < 400 and loc:
        from urllib.parse import urljoin
        url = urljoin(uri, loc)
        return (url if url.startswith(("http://", "https://")) else uri), True
    if status == 200 and body.startswith(expect):
        return PORTAL_FALLBACK, False                   # no portal on this route
    return uri, True


def _kiosk_flag(path: Path, text: str):
    """a flag file for the kiosk supervisor (.xinitrc, user kairos) — it must be able to unlink
    it, so /run/kairos (tmpfiles.d, owned by kairos) and the file itself are the kiosk user's"""
    ids = _kiosk_ids()
    d = path.parent
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        if ids and os.geteuid() == 0:
            os.chown(d, *ids)                # dir owner may unlink whatever is inside
    path.write_text(text)
    try:
        os.chmod(path, 0o666)
        if ids and os.geteuid() == 0:
            os.chown(path, *ids)
    except OSError:
        pass


async def _wifi_signin(nm):
    st = await _wifi_status(nm)
    if not st.get("connected"):
        return {"ok": False, "error": "not_connected", "detail": "join a network first", "url": None}
    url, portal = await _portal_url(st.get("device"))
    try:
        CLOSE_URL.unlink(missing_ok=True)
        _kiosk_flag(OPEN_URL, url + "\n")
    except OSError as e:
        return {"ok": False, "error": "failed", "detail": f"cannot ask the desktop to open the page: {e}", "url": url}
    t = _SIGNIN.get("task")
    if not t or t.done():
        _SIGNIN["task"] = asyncio.ensure_future(_signin_watch(nm, st.get("device")))
    print(f"[kernel] wifi sign-in: opening {url} (portal: {portal}, connectivity: {st.get('connectivity')})", flush=True)
    return {"ok": True, "error": None, "url": url, "portal": portal, "connectivity": st.get("connectivity")}


async def _signin_watch(nm, dev):
    """after signin(): every SIGNIN_POLL s read the device's connectivity, close the window once
    it is online — also when the device left the network or after SIGNIN_LIMIT seconds (Ctrl+W
    closes it too) — and otherwise ask NetworkManager for a new check (bounded: `connectivity
    check` waits for every device's check, 40 s per round in the VM proof)"""
    t0, why = time.monotonic(), "time limit"
    while time.monotonic() - t0 < SIGNIN_LIMIT:
        await asyncio.sleep(SIGNIN_POLL)
        devs, _ = await _nm_devices(nm)
        d = next((x for x in devs or [] if x["device"] == dev or dev is None), None)
        if d is None or not _up(d["state"]):
            why = "the device left the network"
            break
        if d["connectivity"] == "full":
            why = "the device is online"
            break
        await _run([nm, "networking", "connectivity", "check"], timeout=6)
    try:
        _kiosk_flag(CLOSE_URL, "close\n")
    except OSError:
        pass
    print(f"[kernel] wifi sign-in: {why} — sign-in window closed", flush=True)
    invalidate()


async def nm_connectivity():
    """NetworkManager's GLOBAL connectivity (no new check): full|portal|limited|none|unknown, or
    None without NetworkManager — knowledge.online() trusts 'full' / 'none' [R78]"""
    nm = _tool("nmcli")
    if not nm:
        return None
    rc, out = await _run([nm, "-t", "networking", "connectivity"], timeout=5)
    return _norm_conn((out.strip().splitlines() or [""])[-1]) if rc == 0 else None


# ---------------------------------------------------------------- battery (sysfs)

def battery():
    for bat in sorted(SYSFS.glob("class/power_supply/BAT*")):
        cap = _read(bat / "capacity")
        if cap is None:
            continue
        status = _read(bat / "status") or "Unknown"
        ac = None
        for a in SYSFS.glob("class/power_supply/A*"):
            v = _read(a / "online")
            if v is not None:
                ac = v == "1"
        health = None
        full, design = _read(bat / "energy_full") or _read(bat / "charge_full"), \
                       _read(bat / "energy_full_design") or _read(bat / "charge_full_design")
        if full and design and design.isdigit() and int(design) > 0:
            health = round(int(full) / int(design) * 100)
        return {"available": True, "percent": int(cap), "status": status,
                "charging": status == "Charging", "ac_plugged": ac, "health_percent": health}
    return {"available": False, "reason": "no battery (desktop or VM)"}


# ---------------------------------------------------------------- brightness (sysfs)

def brightness_get():
    for bl in sorted(SYSFS.glob("class/backlight/*")):
        cur, mx = _read(bl / "brightness"), _read(bl / "max_brightness")
        if cur and mx and mx.isdigit() and int(mx) > 0:
            return {"available": True, "percent": round(int(cur) / int(mx) * 100),
                    "device": bl.name}
    return {"available": False, "reason": "no backlight (external display or VM)"}


def brightness_set(percent):
    for bl in sorted(SYSFS.glob("class/backlight/*")):
        mx = _read(bl / "max_brightness")
        if mx and mx.isdigit():
            val = max(1, min(100, int(percent))) * int(mx) // 100
            try:
                (bl / "brightness").write_text(str(val))
                return {"ok": True, "percent": int(percent), "device": bl.name}
            except Exception as e:
                return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "no backlight device"}


# ---------------------------------------------------------------- volume (pactl/amixer)
# The sound server (pipewire-pulse) is a per-user session of the kiosk user; the
# kernel runs as root, whose own session has none. Point pactl at the kiosk
# user's socket (root may connect to it) unless the caller set PULSE_SERVER.
RUN_USER = Path(os.environ.get("KAIROS_RUN_USER", "/run/user"))


def _paenv():
    env = dict(os.environ)
    if "PULSE_SERVER" not in env:
        ids = _kiosk_ids()
        cands = [RUN_USER / str(ids[0]) / "pulse" / "native"] if ids else []
        cands += sorted(RUN_USER.glob("*/pulse/native"))
        for sock in cands:
            if sock.exists():
                env["PULSE_SERVER"] = f"unix:{sock}"
                break
    return env


async def _pactl(args, timeout=25):
    return await _run([_tool("pactl"), *args], timeout=timeout, env=_paenv())


async def volume_get():
    pa = _tool("pactl")
    if pa:
        rc, out = await _pactl(["get-sink-volume", "@DEFAULT_SINK@"])
        if rc == 0 and "%" in out:
            pct = out.split("%")[0].rsplit(" ", 1)[-1].strip("/ ")
            if pct.isdigit():
                rc2, mute = await _pactl(["get-sink-mute", "@DEFAULT_SINK@"])
                return {"available": True, "percent": int(pct), "muted": "yes" in mute}
    am = _tool("amixer")
    if am:
        rc, out = await _run([am, "get", "Master"])
        if rc == 0 and "[" in out:
            try:
                pct = int(out.split("[")[1].split("%")[0])
                return {"available": True, "percent": pct, "muted": "[off]" in out}
            except Exception:
                pass
    return {"available": False, "reason": "no audio mixer tool (pactl/amixer)"}


async def volume_set(percent, mute=None):
    pct = max(0, min(100, int(percent)))
    pa = _tool("pactl")
    if pa:
        rc, _ = await _pactl(["set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])
        if mute is not None:
            await _pactl(["set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
        if rc == 0:
            return {"ok": True, "percent": pct}
    am = _tool("amixer")
    if am:
        rc, _ = await _run([am, "set", "Master", f"{pct}%"])
        if rc == 0:
            return {"ok": True, "percent": pct}
    return {"ok": False, "error": "no audio mixer tool"}


# ---------------------------------------------------------------- power

async def power(op, body):
    if not body.get("confirm"):
        return {"ok": False, "error": "pass {\"confirm\": true} — this interrupts the user"}
    cmd = {"off": ["systemctl", "poweroff"], "reboot": ["systemctl", "reboot"],
           "suspend": ["systemctl", "suspend"]}.get(op)
    if not cmd:
        return {"error": f"unknown power op '{op}'"}
    sysctl = _tool("systemctl")
    if not sysctl:
        return {"ok": False, "error": "systemd not available (dev environment)"}
    rc, out = await _run([sysctl, cmd[1]])
    return {"ok": rc == 0, "detail": out.strip()[-200:]}


# ---------------------------------------------------------------- displays (xrandr) [R53]
# The kiosk's X server runs as user "kairos" on :0; the kernel (root) reaches it
# with that DISPLAY + XAUTHORITY. Layout is saved so .xinitrc re-applies it on boot.
DISPLAY_STATE = Path(os.environ.get("VIBEOS_DIR", "/opt/vibeos")) / "cache" / "display.json"
# The reload flag is written by the kernel (root) and REMOVED by the kiosk
# supervisor in .xinitrc (user kairos). It must not live in sticky /tmp: there a
# non-root user cannot unlink a root-owned file, the flag stuck, and the supervisor
# relaunched every Chromium every 3 s until reboot. /run/kairos is created by
# tmpfiles.d/kairos.conf owned by the kiosk user; the same path is in .xinitrc.
KIOSK_RELOAD = Path(os.environ.get("KAIROS_KIOSK_RELOAD", "/run/kairos/kiosk-reload"))
KIOSK_USER = os.environ.get("KAIROS_KIOSK_USER", "kairos")


def _kiosk_ids():
    """(uid, gid) of the kiosk user, or None outside the appliance."""
    try:
        import pwd
        p = pwd.getpwnam(KIOSK_USER)
        return p.pw_uid, p.pw_gid
    except (KeyError, ImportError):
        return None


def _touch_reload():
    """Ask the kiosk supervisor to relaunch; the flag must be removable by it (_kiosk_flag)."""
    _kiosk_flag(KIOSK_RELOAD, "reload")


X11_SOCKETS = Path(os.environ.get("KAIROS_X11_SOCKETS", "/tmp/.X11-unix"))


def _display() -> str:
    """The kiosk's display. .bash_profile pins X to :0, but a display that is not
    there is never addressed blindly: if X0's socket is missing and another X socket
    exists (an X that came up as :1 after a restart — D5), that display is used, so
    the display API and xrandr never talk to a dead :0."""
    want = os.environ.get("KAIROS_DISPLAY", ":0")
    try:
        if (X11_SOCKETS / f"X{want.lstrip(':').split('.')[0]}").exists():
            return want
        live = sorted(int(p.name[1:]) for p in X11_SOCKETS.glob("X[0-9]*") if p.name[1:].isdigit())
        if live:
            return f":{live[0]}"
    except OSError:
        pass
    return want


def _xenv():
    env = dict(os.environ)
    env.setdefault("DISPLAY", _display())
    env.setdefault("XAUTHORITY", os.environ.get("KAIROS_XAUTHORITY",
                                                "/home/kairos/.Xauthority"))
    return env


async def _xrandr(args, timeout=25):
    xr = _tool("xrandr")
    if not xr:
        return -1, "xrandr not installed"
    return await _run([xr, *args], timeout=timeout, env=_xenv())


def _parse_xrandr(text):
    """Parse `xrandr --query` into a list of outputs with modes + current geometry."""
    import re
    outputs = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"^(\S+) (connected|disconnected)(.*)$", line)
        if m:
            name, state, rest = m.group(1), m.group(2), m.group(3)
            geo = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", rest)
            # the rotation keyword sits between the geometry and the "(normal left
            # inverted right)" capability list — never match inside that list
            head = rest.split("(")[0].split()
            cur = {"output": name, "connected": state == "connected",
                   "primary": "primary" in head,
                   "resolution": (f"{geo.group(1)}x{geo.group(2)}" if geo else None),
                   "position": ([int(geo.group(3)), int(geo.group(4))] if geo else None),
                   "rotation": next((r for r in ("left", "right", "inverted")
                                     if r in head), "normal"),
                   "modes": []}
            outputs.append(cur)
            continue
        m = re.match(r"^\s+(\d+x\d+)\s", line)
        if m and cur is not None:
            cur["modes"].append(m.group(1))
    return outputs


async def _layout_args(query_text=None):
    """xrandr args reproducing the CURRENT layout of every connected output (from
    `xrandr --query`): what kairos-display-apply replays at boot. None on failure."""
    if query_text is None:
        rc, query_text = await _xrandr(["--query"])
        if rc != 0:
            return None
    args = []
    for o in _parse_xrandr(query_text):
        if not o["connected"]:
            continue
        args += ["--output", o["output"]]
        if not o["resolution"]:            # connected but switched off
            args += ["--off"]
            continue
        args += ["--mode", o["resolution"],
                 "--pos", f"{o['position'][0]}x{o['position'][1]}",
                 "--rotate", o["rotation"]]
        if o["primary"]:
            args += ["--primary"]
    return args or None


async def display_list():
    rc, out = await _xrandr(["--query"])
    if rc != 0:
        return {"available": False,
                "reason": "no X display / xrandr (headless or pre-kiosk)"}
    outs = _parse_xrandr(out)
    conn = [o for o in outs if o["connected"]]
    return {"available": True, "count": len(conn),
            "multiscreen": len(conn) > 1, "displays": outs}


async def display_set(body):
    """op=set: resolution/position/rotation/primary/off for one output.
    op=arrange: {mode:"extend"|"mirror"} across all connected outputs.
    Layout is persisted and the kiosk is asked to relaunch its per-screen windows."""
    op = body.get("op")
    if op == "arrange":
        rc, out = await _xrandr(["--query"])
        conn = [o["output"] for o in _parse_xrandr(out) if o["connected"]] if rc == 0 else []
        if not conn:
            return {"ok": False, "error": "no connected displays"}
        mode = body.get("mode", "extend")
        args, prev = [], None
        for o in conn:
            args += ["--output", o, "--auto"]
            if mode == "mirror" and prev:
                args += ["--same-as", prev]
            elif mode == "extend" and prev:
                args += ["--right-of", prev]
            if not prev:
                args += ["--primary"]
            prev = o
        rc, out = await _xrandr(args)
    else:  # single-output set
        out_name = body.get("output")
        if not out_name:
            return {"ok": False, "error": "output required"}
        args = ["--output", out_name]
        if body.get("off"):
            args += ["--off"]
        else:
            args += ["--auto"] if not body.get("resolution") else \
                    ["--mode", body["resolution"]]
            if body.get("primary"):
                args += ["--primary"]
            if body.get("position"):
                args += ["--pos", f"{int(body['position'][0])}x{int(body['position'][1])}"]
            if body.get("rotation"):
                args += ["--rotate", body["rotation"]]
            if body.get("brightness") is not None:  # xrandr software brightness 0.1-1.0
                args += ["--brightness", str(max(0.1, min(1.0, body["brightness"] / 100)))]
        rc, res = await _xrandr(args)
        out = res
    if rc != 0:
        return {"ok": False, "error": out.strip()[-200:]}
    saved = args
    if op != "arrange":
        # a single-output tweak must not be the ONLY thing replayed at boot (the
        # other monitors stayed dark: kairos-display-apply exits 0 on any saved
        # layout, so .xinitrc never auto-extended) — persist the whole layout
        full = await _layout_args()
        if full:
            if body.get("brightness") is not None and "--brightness" in args:
                # software brightness is not in --query: keep it for this output
                i = full.index(out_name) + 1
                full[i:i] = ["--brightness", args[args.index("--brightness") + 1]]
            saved = full
    try:  # persist the xrandr args so .xinitrc re-applies the layout on boot,
        # then ask the kiosk supervisor to relaunch one Chromium per screen
        import json as _json
        DISPLAY_STATE.parent.mkdir(parents=True, exist_ok=True)
        DISPLAY_STATE.write_text(_json.dumps({"xrandr_args": saved, "request": body}))
        _touch_reload()
    except Exception:
        pass
    return {"ok": True, "reloading_kiosk": True}


# ---------------------------------------------------------------- date / time (timedatectl) [R54]

async def datetime_get():
    tc = _tool("timedatectl")
    if not tc:
        return {"available": False, "reason": "timedatectl not available"}
    rc, out = await _run([tc, "show"])
    if rc != 0:
        return {"available": False, "reason": "timedatectl failed"}
    kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    return {"available": True, "timezone": kv.get("Timezone"),
            "ntp": kv.get("NTP") == "yes", "ntp_synced": kv.get("NTPSynchronized") == "yes",
            "rtc_utc": kv.get("LocalRTC") == "no"}


async def datetime_set(body):
    tc = _tool("timedatectl")
    if not tc:
        return {"ok": False, "error": "timedatectl not available"}
    if "timezone" in body:
        rc, out = await _run([tc, "set-timezone", body["timezone"]])
    elif "ntp" in body:
        rc, out = await _run([tc, "set-ntp", "true" if body["ntp"] else "false"])
    elif "time" in body:  # "YYYY-MM-DD HH:MM:SS"
        rc, out = await _run([tc, "set-time", body["time"]])
    else:
        return {"ok": False, "error": "pass timezone | ntp | time"}
    return {"ok": rc == 0, "detail": out.strip()[-200:]}


async def timezones():
    tc = _tool("timedatectl")
    rc, out = await _run([tc, "list-timezones"]) if tc else (-1, "")
    return {"available": rc == 0, "timezones": out.split() if rc == 0 else []}


# ---------------------------------------------------------------- bluetooth (bluez) [R55, R78]
# status() keeps {available, powered, connected:[names]} (objects there would print [object
# Object] in apps generated before R78) and adds blocked + devices:[{mac, name, kind, paired,
# trusted, connected, battery}]. pair() = pair -> trust -> connect (trust = bluez reconnects the
# mouse / earbuds by itself after a reboot). off = rfkill block (systemd-rfkill restores it at
# boot; a bluez "power off" was undone by AutoEnable), on = unblock + power on.
BT_KINDS = (("audio-", "audio"), ("input-mouse", "mouse"), ("input-tablet", "mouse"),
            ("input-keyboard", "keyboard"), ("input-gaming", "gamepad"), ("phone", "phone"))
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]|[\x01\x02\r\x07]")
PAIR_DONE = (r"Pairing successful|Failed to pair[^\n]*|AlreadyExists|Device [0-9A-Fa-f:]{17} not available"
             r"|not available")
_PAIRING = {}        # the last pair(): {mac, state: pairing|enter_code|paired|failed, code, error, t, task}


def bt_kind(icon) -> str:
    """audio | mouse | keyboard | gamepad | phone | other, from bluez's Icon (Appearance/Class)"""
    icon = (icon or "").lower()
    return next((k for p, k in BT_KINDS if icon.startswith(p)), "other")


def _bt_clean(text) -> str:
    return _ANSI.sub("", text or "")


def _bt_num(v):
    m = re.search(r"\((-?\d+)\)\s*$", v or "") or re.match(r"\s*(-?\d+)\s*$", v or "")
    return int(m.group(1)) if m else None


def _bt_info(text) -> dict:
    """`bluetoothctl info MAC` -> {name, icon, paired, trusted, connected, battery, rssi}"""
    kv = {}
    for line in _bt_clean(text).splitlines():
        k, sep, v = line.strip().partition(": ")
        if sep and k not in kv:
            kv[k] = v.strip()
    return {"name": kv.get("Name") or None, "icon": kv.get("Icon", ""), "paired": kv.get("Paired") == "yes",
            "trusted": kv.get("Trusted") == "yes", "connected": kv.get("Connected") == "yes",
            "battery": _bt_num(kv.get("Battery Percentage")), "rssi": _bt_num(kv.get("RSSI"))}


def _bt_list(text) -> list:
    """`bluetoothctl devices [Paired|Connected]` -> [(MAC, name)] (event lines like "[NEW] Device" skipped)"""
    out = []
    for line in _bt_clean(text).splitlines():
        p = line.strip().split(" ", 2)
        if len(p) >= 2 and p[0] == "Device" and MAC_RE.match(p[1]):
            out.append((p[1].upper(), p[2] if len(p) > 2 else p[1]))
    return out


class _BtAgent:
    """ONE long-lived interactive `bluetoothctl` on a pseudo-terminal (stdlib pty — no new Python
    dependency; bluetoothctl shows its agent prompts only on a terminal), registered as the pairing
    agent (KeyboardDisplay) so devices that need a code can pair. Prompts are answered only while
    one of OUR pair() calls runs ("armed"): numeric comparison -> yes (the code is shown to the
    user too), a passkey / PIN to TYPE on a keyboard -> surfaced as `code`, outside a pair() -> no.
    Scans run here as well: the [NEW]/[CHG] events say what was seen in THIS scan."""
    PROMPTS = (
        (re.compile(r"Confirm passkey (\d+) \(yes/no\):"), "confirm"),
        (re.compile(r"Accept pairing \(yes/no\):"), "yes"),
        (re.compile(r"Authorize service [^\n]*?\(yes/no\):"), "yes"),
        (re.compile(r"Enter PIN code:"), "pin"),
        (re.compile(r"Enter passkey \(number in 0-999999\):"), "passkey"),
        (re.compile(r"Passkey: (\d{6})[ \t]*\n"), "show"),
        (re.compile(r"PIN code: (\w{4,16})[ \t]*\n"), "show"),
    )

    def __init__(self):
        self.proc = self.fd = self.loop = self.event = None
        self.text, self.pending, self.base, self.done_to = "", "", 0, 0
        self.armed_until = 0.0
        self.code = None
        self.dec = None

    def alive(self):
        return (self.proc is not None and self.proc.returncode is None and self.fd is not None
                and self.loop is asyncio.get_running_loop())

    async def start(self, bc) -> bool:
        if self.alive():
            return True
        await self.stop()
        import codecs
        import fcntl
        import pty
        import struct
        import termios
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 500, 0, 0))   # no readline wraps
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        except Exception:
            pass
        try:
            self.proc = await asyncio.create_subprocess_exec(
                bc, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                env=dict(os.environ, TERM="dumb", NO_COLOR="1"))
        except Exception as e:
            os.close(master)
            os.close(slave)
            self.proc = None
            print(f"[kernel] bt-agent: bluetoothctl did not start: {e}", flush=True)
            return False
        os.close(slave)
        os.set_blocking(master, False)
        self.fd, self.loop, self.event = master, asyncio.get_running_loop(), asyncio.Event()
        self.text, self.pending, self.base, self.done_to = "", "", 0, 0
        self.dec = codecs.getincrementaldecoder("utf-8")("ignore")
        self.loop.add_reader(master, self._readable)
        m = self.mark()
        self.send("agent KeyboardDisplay")
        await self.wait(m, r"Agent registered|already registered|Failed to register", 6)
        m = self.mark()
        self.send("default-agent")
        got = await self.wait(m, r"Default agent request successful|Failed to request default agent"
                                 r"|No agent is registered", 6)
        print(f"[kernel] bt-agent: bluetoothctl pid {self.proc.pid} — {got or 'no answer to default-agent'}", flush=True)
        return self.alive()

    async def stop(self):
        if self.fd is not None:
            try:
                (self.loop or asyncio.get_running_loop()).remove_reader(self.fd)
            except Exception:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            if self.loop is asyncio.get_running_loop():
                await _reap(self.proc)
        self.proc = None

    def _readable(self):
        try:
            chunk = os.read(self.fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:                          # EIO: bluetoothctl closed its terminal (exited)
            try:
                self.loop.remove_reader(self.fd)
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
            self.event.set()
            return
        raw = self.pending + self.dec.decode(chunk)
        cut = re.search(r"\x1b[^A-Za-z]{0,12}$", raw)      # an escape split across two reads
        self.pending, raw = (raw[cut.start():], raw[:cut.start()]) if cut else ("", raw)
        self.text += _bt_clean(raw)
        if len(self.text) > 400000:            # keep the tail; marks are stream offsets
            drop = len(self.text) - 200000
            self.text, self.base = self.text[drop:], self.base + drop
        self._prompts()
        self.event.set()

    def _prompts(self):
        start = max(self.done_to - self.base, 0)
        hits = sorted(((m.start(), m.end(), what, m) for rx, what in self.PROMPTS
                       for m in rx.finditer(self.text, start)), key=lambda h: h[0])
        for s, e, what, m in hits:
            if self.base + s < self.done_to:
                continue
            armed = time.monotonic() < self.armed_until
            if what == "confirm":
                self.code = m.group(1)
                self.send("yes" if armed else "no")
            elif what == "yes":
                self.send("yes" if armed else "no")
            elif what == "pin":
                self.send("0000" if armed else "")
            elif what == "passkey":
                self.send("")                  # the other side shows a code we cannot know: refuse
            else:
                self.code = m.group(1)
            print(f"[kernel] bt-agent: {m.group(0).strip()} -> "
                  f"{'shown to the user' if what == 'show' else ('answered' if armed else 'refused (no pair() running)')}",
                  flush=True)
            self.done_to = self.base + e

    def mark(self) -> int:
        return self.base + len(self.text)

    def since(self, mark) -> str:
        return self.text[max(mark - self.base, 0):]

    def send(self, line: str):
        if self.fd is not None:
            try:
                os.write(self.fd, (line + "\n").encode())
            except OSError:
                pass

    async def wait(self, mark, pattern, timeout):
        """the first match of pattern in the output after mark, or None after timeout"""
        rx, t_end = re.compile(pattern), time.monotonic() + timeout
        while True:
            self.event.clear()
            m = rx.search(self.text, max(mark - self.base, 0))
            if m:
                return m.group(0)
            left = t_end - time.monotonic()
            if left <= 0 or self.fd is None:
                return None
            try:
                await asyncio.wait_for(self.event.wait(), left)
            except asyncio.TimeoutError:
                pass


AGENT = _BtAgent()
NO_ADAPTER = "no Bluetooth adapter found"


def _bt_adapters() -> list:
    """the kernel's Bluetooth controllers (hci0 …) — present even while rfkill-blocked. Without
    one, bluetoothd does not run and `bluetoothctl show` waits for it until its timeout (6 s in
    the VM proof): status()/vibe.hw.get() answer at once instead"""
    return sorted(p.name for p in SYSFS.glob("class/bluetooth/hci*"))


async def bluetooth(op, body):
    bc = _tool("bluetoothctl")
    if not bc:
        return {"available": False, "reason": "bluetooth stack (bluez) not installed"}
    if not _bt_adapters() and op not in ("connect", "disconnect", "remove"):
        blocked = any(_rfkill("bluetooth")[1:])
        if op in ("", "status"):
            return {"available": False, "reason": NO_ADAPTER, "powered": False, "blocked": blocked,
                    "connected": [], "devices": []}
        if op in ("on", "off", "pair"):
            return {"ok": False, "error": "unsupported", "detail": NO_ADAPTER}
        if op == "scan":
            return {"available": False, "reason": NO_ADAPTER, "devices": []}
    if op in ("", "status"):
        return await _memo("bt", lambda: _bt_status(bc))
    if op in ("on", "off"):
        return _changed(await _bt_power(bc, op == "on"))
    if op == "scan":
        return await _bt_scan(bc, body)
    if op == "pair":
        return _changed(await _bt_pair(bc, await _bt_resolve(bc, body.get("mac") or body.get("name") or "")))
    if op in ("connect", "disconnect", "remove"):
        who = body.get("mac") or body.get("name") or ""
        if not who:
            return {"ok": False, "error": "mac required"}
        mac = await _bt_resolve(bc, who)
        if not mac:
            return {"ok": False, "error": "bad_mac", "detail": "a device address looks like AA:BB:CC:DD:EE:FF "
                                                               "(or pass the exact name of a known device)"}
        rc, out = await _run([bc, op, mac], timeout=30)
        out = _bt_clean(out)
        ok = rc == 0 and "Failed" not in out and "not available" not in out
        res = {"ok": ok, "error": None if ok else ("not_in_range" if "not available" in out else "failed"),
               "mac": mac, "detail": out.strip()[-200:]}
        if ok and op == "connect":
            rc, info = await _run([bc, "info", mac], timeout=6)
            if bt_kind(_bt_info(info)["icon"]) == "audio":
                res["sound"] = await _bt_audio_sink(mac)
        return _changed(res)
    return {"error": f"unknown bluetooth op '{op}' — status|on|off|scan|pair|connect|disconnect|remove"}


async def _bt_resolve(bc, who) -> str:
    """a device address — or a device NAME, as generated apps pass what status().connected lists (names,
    kept for old apps): resolved against the devices bluez knows, exact and case-insensitive, one match
    only (GPU run 4: Gemma's Disconnect button sent the name). "" when nothing (unique) matches."""
    who = (who or "").strip()
    if MAC_RE.match(who):
        return who.upper()
    if not who:
        return ""
    rc, out = await _run([bc, "devices"], timeout=6)
    hits = [m for m, n in _bt_list(out) if n.strip().lower() == who.lower()]
    return hits[0] if len(hits) == 1 else ""


async def _bt_status(bc):
    present, soft, hard = _rfkill("bluetooth")
    blocked = soft or hard
    rc, show = await _run([bc, "show"], timeout=6)
    show = _bt_clean(show)
    if "Controller" not in show:
        reason = ("Bluetooth is switched off (airplane mode)" if blocked
                  else "the Bluetooth service is not answering" if "timed out" in show or "Waiting" in show
                  else "no Bluetooth adapter found")
        return {"available": False, "reason": reason, "powered": False, "blocked": blocked,
                "connected": [], "devices": []}
    rc, conn = await _run([bc, "devices", "Connected"], timeout=6)
    rc, paired = await _run([bc, "devices", "Paired"], timeout=6)
    macs = dict(_bt_list(paired))
    for mac, name in _bt_list(conn):
        macs.setdefault(mac, name)
    devices = []
    for mac, name in list(macs.items())[:16]:
        rc, info = await _run([bc, "info", mac], timeout=6)
        i = _bt_info(info)
        if not (i["paired"] or i["connected"]):      # an old bluez ignoring the "Paired" filter
            continue
        devices.append({"mac": mac, "name": i["name"] or name, "kind": bt_kind(i["icon"]), "paired": i["paired"],
                        "trusted": i["trusted"], "connected": i["connected"], "battery": i["battery"]})
    res = {"available": True, "powered": "Powered: yes" in show, "blocked": blocked,
           "connected": [n for _, n in _bt_list(conn)], "devices": devices}
    if blocked:
        res["reason"] = ("Bluetooth is switched off by the hardware switch" if hard
                         else "Bluetooth is off — vibe.hw.bluetooth.on()")
    if _PAIRING and (_PAIRING.get("state") in ("pairing", "enter_code") or time.time() - _PAIRING.get("t", 0) < 120):
        res["pairing"] = {k: _PAIRING.get(k) for k in ("mac", "state", "code", "error")}
    return res


async def _bt_power(bc, on):
    rk = _tool("rfkill")
    if not on:
        if rk:
            rc, out = await _run([rk, "block", "bluetooth"])
            return {"ok": rc == 0, "error": None if rc == 0 else "failed", "blocked": rc == 0,
                    "powered": False if rc == 0 else None, "detail": out.strip()[-160:]}
        rc, out = await _run([bc, "power", "off"])      # no rfkill tool: bluez AutoEnable undoes it at boot
        return {"ok": rc == 0, "error": None if rc == 0 else "failed", "blocked": False,
                "powered": False if rc == 0 else None, "detail": _bt_clean(out).strip()[-160:]}
    if _rfkill("bluetooth")[2]:
        return {"ok": False, "error": "failed", "blocked": True, "powered": False,
                "detail": "Bluetooth is switched off by the hardware switch"}
    if rk:
        await _run([rk, "unblock", "bluetooth"])
    rc, out = -1, ""
    for _ in range(8):                                  # the adapter needs a moment after the unblock
        rc, out = await _run([bc, "power", "on"], timeout=8)
        if rc == 0 and "succeeded" in _bt_clean(out):
            break
        await asyncio.sleep(0.5)
    ok = rc == 0 and "succeeded" in _bt_clean(out)
    return {"ok": ok, "error": None if ok else "failed", "blocked": False, "powered": ok,
            "detail": _bt_clean(out).strip()[-160:]}


async def _bt_scan(bc, body):
    """only devices seen in THIS scan, not yet paired, with a name (unnamed = beacons and phones'
    random addresses), strongest first: [{mac, name, kind, rssi, paired:false}]. A read: it never
    switches the adapter on (that is vibe.hw.bluetooth.on())."""
    present, soft, hard = _rfkill("bluetooth")
    if soft or hard:
        return {"available": True, "blocked": True, "powered": False, "devices": [],
                "reason": "Bluetooth is switched off — vibe.hw.bluetooth.on()"}
    try:
        secs = min(max(float(body.get("seconds") or 10), 1.0), 30.0)
    except (TypeError, ValueError):
        secs = 10.0
    rc, show = await _run([bc, "show"], timeout=6)
    show = _bt_clean(show)
    if "Controller" not in show:
        return {"available": False, "reason": "no Bluetooth adapter found", "devices": []}
    if "Powered: yes" not in show:
        return {"available": True, "powered": False, "devices": [],
                "reason": "Bluetooth is off — vibe.hw.bluetooth.on()"}
    if not await AGENT.start(bc):
        return {"available": False, "reason": "bluetoothctl did not start", "devices": []}
    m = AGENT.mark()
    AGENT.send("scan on")
    await asyncio.sleep(secs)
    AGENT.send("scan off")
    seen = {}
    for line in AGENT.since(m).splitlines():
        ev = re.search(r"\[(NEW|CHG)\]\s+Device\s+([0-9A-Fa-f:]{17})\s*(.*)$", line)
        if not ev:
            continue
        d = seen.setdefault(ev.group(2).upper(), {})
        r = re.match(r"RSSI:\s*(?:0x[0-9a-fA-F]+\s*\()?(-?\d+)", ev.group(3))
        if r:
            d["rssi"] = int(r.group(1))
    sem = asyncio.Semaphore(6)

    async def info(mac):
        async with sem:
            rc, out = await _run([bc, "info", mac], timeout=6)
            return mac, _bt_info(out)
    devices = []
    for mac, i in await asyncio.gather(*(info(mac) for mac in list(seen)[:40])):
        if i["paired"] or not i["name"]:
            continue
        rssi = seen[mac].get("rssi", i["rssi"])
        devices.append({"mac": mac, "name": i["name"], "kind": bt_kind(i["icon"]), "rssi": rssi, "paired": False})
    devices.sort(key=lambda d: -(d["rssi"] if d["rssi"] is not None else -999))
    return {"available": True, "powered": True, "devices": devices, "seconds": secs}


async def _bt_pair(bc, mac):
    if not MAC_RE.match(mac or ""):
        return {"ok": False, "error": "bad_mac", "detail": "a device address looks like AA:BB:CC:DD:EE:FF"}
    mac = mac.upper()
    task = _PAIRING.get("task")
    if task and not task.done():
        busy = _PAIRING.get("mac") == mac and _PAIRING.get("state") == "enter_code"
        return {"ok": False, "error": "enter_code" if busy else "busy", "pending": True, "code": _PAIRING.get("code"),
                "mac": mac, "detail": f"pairing {_PAIRING.get('mac')} is still waiting for its code"}
    if not await AGENT.start(bc):
        return {"ok": False, "error": "failed", "detail": "the pairing agent (bluetoothctl) did not start"}
    _PAIRING.clear()
    _PAIRING.update(mac=mac, state="pairing", code=None, error=None, t=time.time())
    AGENT.code = None
    AGENT.armed_until = time.monotonic() + 150
    m = AGENT.mark()
    AGENT.send(f"pair {mac}")
    hit = await AGENT.wait(m, PAIR_DONE + r"|Passkey: \d{6}[ \t]*\n|PIN code: \w{4,16}[ \t]*\n", 30)
    if hit and ("Passkey:" in hit or "PIN code:" in hit):
        _PAIRING.update(state="enter_code", code=AGENT.code)
        _PAIRING["task"] = asyncio.ensure_future(_bt_pair_finish(bc, mac, m))
        print(f"[kernel] bluetooth pair {mac}: waiting for the code to be typed on the device", flush=True)
        return {"ok": False, "error": "enter_code", "pending": True, "code": AGENT.code, "mac": mac,
                "detail": f"type {AGENT.code} on the keyboard and press Enter — status() shows it paired"}
    return await _bt_pair_result(bc, mac, hit, AGENT.since(m))


async def _bt_pair_finish(bc, mac, mark):
    hit = await AGENT.wait(mark, PAIR_DONE, 120)
    await _bt_pair_result(bc, mac, hit, AGENT.since(mark))
    invalidate()


async def _bt_pair_result(bc, mac, hit, transcript):
    AGENT.armed_until = 0.0
    err = None
    if hit is None:
        err = "timeout"
    elif "Pairing successful" in hit or "AlreadyExists" in hit:
        err = None
    elif "not available" in hit or "ConnectionAttemptFailed" in hit or "Page Timeout" in hit:
        err = "not_in_range"
    elif re.search(r"Authentication(Failed|Rejected|Canceled|Timeout)", hit):
        err = "auth_failed"
    elif "InProgress" in hit:
        err = "busy"
    else:
        err = "failed"
    if err:
        _PAIRING.update(state="failed", error=err)
        tail = " | ".join(l.strip() for l in transcript.splitlines() if l.strip())[-300:]
        print(f"[kernel] bluetooth pair {mac}: {err} — {tail}", flush=True)
        return {"ok": False, "error": err, "code": AGENT.code, "mac": mac,
                "detail": (hit or "no answer from the device within 30 s")[:200]}
    m = AGENT.mark()
    AGENT.send(f"trust {mac}")
    tr = await AGENT.wait(m, r"trust succeeded|Failed to set trusted[^\n]*|not available", 8)
    m = AGENT.mark()
    AGENT.send(f"connect {mac}")
    cn = await AGENT.wait(m, r"Connection successful|Failed to connect[^\n]*|not available", 25)
    rc, out = await _run([bc, "info", mac], timeout=6)
    i = _bt_info(out)
    res = {"ok": True, "error": None, "mac": mac, "name": i["name"], "kind": bt_kind(i["icon"]), "paired": True,
           "trusted": bool(tr and "succeeded" in tr) or i["trusted"],
           "connected": bool(cn and "successful" in cn) or i["connected"], "code": AGENT.code}
    if not res["connected"]:
        res["detail"] = (cn or "no answer to connect")[:200]
    if res["kind"] == "audio" and res["connected"]:
        res["sound"] = await _bt_audio_sink(mac)
    _PAIRING.update(state="paired", error=None)
    print(f"[kernel] bluetooth pair {mac} ({res['name']}, {res['kind']}): paired, trusted {res['trusted']}, "
          f"connected {res['connected']}{', sound ' + str(res.get('sound')) if 'sound' in res else ''}", flush=True)
    return res


async def _bt_audio_sink(mac, settle=1.5):
    """after headphones connect: their PipeWire sink and whether WirePlumber made it the default by
    itself (default_by "wireplumber") — if not, kAIrOS does (default_by "kairos")"""
    if not _tool("pactl"):
        return {"sink": None, "default_by": None}
    key, sink = mac.replace(":", "_").upper(), None
    for _ in range(16):
        rc, out = await _pactl(["list", "short", "sinks"])
        sink = next((l.split("\t")[1] for l in out.splitlines()
                     if "\t" in l and key in l.split("\t")[1].upper()), None)
        if sink:
            break
        await asyncio.sleep(0.5)
    if not sink:
        return {"sink": None, "default_by": None}
    await asyncio.sleep(settle)                          # give WirePlumber its moment to switch by itself
    rc, cur = await _pactl(["get-default-sink"])
    if cur.strip() == sink:
        return {"sink": sink, "default_by": "wireplumber"}
    rc, _ = await _pactl(["set-default-sink", sink])
    return {"sink": sink, "default_by": "kairos" if rc == 0 else None}


# ---------------------------------------------------------------- airplane mode [R78]
# Both radios at once. Hardware state stays OUT of vibe.settings: NetworkManager persists the
# WiFi radio (NetworkManager.state), systemd-rfkill the Bluetooth block — a settings copy would drift.

def _airplane_from(wifi_st, bt_st) -> bool:
    w = wifi_st.get("available") and wifi_st.get("enabled") is not False
    b = bt_st.get("available") and not bt_st.get("blocked")
    return bool((wifi_st.get("available") or bt_st.get("available") or bt_st.get("blocked")) and not w and not b)


async def airplane(op, body):
    nm, rk = _tool("nmcli"), _tool("rfkill")
    if op in ("", "get", "status"):
        wifi_on = None
        if nm:
            rc, out = await _run([nm, "-t", "-f", "WIFI", "radio"], timeout=6)
            if rc == 0:
                wifi_on = (out.strip().splitlines() or [""])[-1] == "enabled"
        if wifi_on and _rfkill("wlan")[2]:
            wifi_on = False                               # hardware switch
        bt, bsoft, bhard = _rfkill("bluetooth")
        radios = wifi_on is not None or bt
        on = radios and wifi_on is not True and (not bt or bsoft or bhard)
        return {"available": bool(radios), "airplane": bool(on), "wifi_enabled": wifi_on,
                "bluetooth_blocked": (bsoft or bhard) if bt else None}
    if op == "set":
        _JOIN["gen"] += 1
        want = body.get("on", body.get("value", body.get("enabled")))
        want = want if isinstance(want, bool) else str(want).lower() in ("1", "true", "on", "yes")
        steps = []
        if want:
            if nm:
                steps.append(await _run([nm, "radio", "all", "off"]))
            if rk:
                steps.append(await _run([rk, "block", "bluetooth"]))
        else:
            if rk:
                steps.append(await _run([rk, "unblock", "wlan"]))
            if nm:
                steps.append(await _run([nm, "radio", "all", "on"]))
            bc = _tool("bluetoothctl")
            if bc and _rfkill("bluetooth")[0]:
                r = await _bt_power(bc, True)
                steps.append((0 if r["ok"] else 1, r.get("detail", "")))
        ok = bool(steps) and all(rc == 0 for rc, _ in steps)
        return _changed({"ok": ok, "error": None if ok else "failed", "airplane": want,
                         "detail": "; ".join(o.strip()[-80:] for rc, o in steps if rc != 0)})
    return {"error": f"unknown airplane op '{op}' — get|set"}


# ---------------------------------------------------------------- sound output device (pactl) [R56]

async def sound_devices(op, body):
    pa = _tool("pactl")
    if not pa:
        return {"available": False, "reason": "no PulseAudio/PipeWire (pactl)"}
    if op == "set":
        sink = body.get("sink")
        if not sink:
            return {"ok": False, "error": "sink required"}
        rc, out = await _pactl(["set-default-sink", sink])
        return {"ok": rc == 0, "detail": out.strip()[-160:]}
    rc, out = await _pactl(["list", "short", "sinks"])
    rc2, cur = await _pactl(["get-default-sink"])
    default = cur.strip()
    sinks = [{"name": l.split("\t")[1], "default": l.split("\t")[1] == default}
             for l in out.splitlines() if "\t" in l]
    return {"available": rc == 0, "sinks": sinks}


# ---------------------------------------------------------------- summary

async def summary():
    """GET /api/hw — one call that tells an app everything about this machine."""
    disk = shutil.disk_usage("/")
    mem_total = mem_avail = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                mem_avail = int(line.split()[1]) // 1024
    except Exception:
        pass
    wifi_st = await wifi("status", {})           # both cached ~2 s [R78]: a 5-s poll spawns no tools twice
    bt_st = await bluetooth("status", {})
    return {
        "wifi": wifi_st,
        "battery": battery(),
        "brightness": brightness_get(),
        "volume": await volume_get(),
        "display": await display_list(),
        "bluetooth": bt_st,
        "airplane": _airplane_from(wifi_st, bt_st),
        "datetime": await datetime_get(),
        "memory_mb": {"total": mem_total, "available": mem_avail},
        "disk_gb": {"total": round(disk.total / 1e9, 1), "free": round(disk.free / 1e9, 1)},
    }


async def handle(body: dict):
    """Dispatch for POST /api/hw: {"device": …, "op": …, …}."""
    device, op = body.get("device", ""), body.get("op", "")
    if device in ("", "all", "summary"):
        return await summary()
    if device == "wifi":
        return await wifi(op or "status", body)
    if device == "battery":
        return battery()
    if device == "brightness":
        if op == "set":
            return brightness_set(body.get("percent", 50))
        return brightness_get()
    if device == "volume":
        if op == "set":
            return await volume_set(body.get("percent", 50), body.get("mute"))
        return await volume_get()
    if device == "power":
        return await power(op, body)
    if device == "display":
        if op in ("set", "arrange"):
            return await display_set(body)
        return await display_list()
    if device == "datetime":
        if op == "set":
            return await datetime_set(body)
        if op == "timezones":
            return await timezones()
        return await datetime_get()
    if device == "bluetooth":
        return await bluetooth(op or "status", body)
    if device == "airplane":
        return await airplane(op or "get", body)
    if device == "sound":
        return await sound_devices(op or "list", body)
    return {"error": f"unknown device '{device}' — use wifi|battery|brightness|volume|"
                     "power|display|datetime|bluetooth|airplane|sound|all"}
