#!/usr/bin/env python3
"""scripts/public-image-scrub.py — the raw-byte half of scripts/public-image-prep.sh [R88, docs/PUBLIC-IMAGES-2026-10-16.md].

The prep script's file pass removes every private FILE, but the bytes of what it removed — and what the running system
left — stay on the disk: the v7.5-2 public build found the WiFi PSK in /swapfile (fallocated: unwritten extents over
old swapped-out memory pages; the FS reads them as zeros, the image carries them), in FREE ext4 blocks (fstrim on a
qcow2 overlay drops only whole 64 KiB clusters) and in a free cluster of the ESP — after prep said PASS. This script
works on the UNMOUNTED disk (a flat image file, the build's hot-plugged /dev/sdX, one partition or a bare file system):

  scrub   every ext4 file system: the swapfile(s) /etc/fstab names — every page but the header zeroed on the raw device,
          the header page rebuilt as mkswap writes it with the SAME UUID / label / size (nothing else in the FS changes),
          then every free block that is not zero (debugfs dump_unused: the bitmaps through libext2fs) zeroed; every FAT
          file system (the ESP): every free cluster (free in every FAT copy) that is not zero zeroed. Only non-zero
          blocks are written, so a second run writes nothing (and a qcow2 overlay does not grow). Refuses a mounted or
          loop-attached disk and an ext4 that is not clean. Then the verify below.
  verify  read-only: (1) structure — no swapfile page but its header, no free ext4 block and no free FAT cluster holds a
          non-zero byte; (2) needles — the raw bytes of the whole disk searched for the private strings (read in chunks; the
          needles come from a 0600 file and are NEVER printed, only their names): a hit in a free block, a swapfile, the
          journal, metadata (a directory's names), a free or a metadata ESP cluster or outside the partitions is a FAIL;
          a hit inside a regular file is a WARN (the prep script's file verify judges files; shipped repo text is public).

  public-image-scrub.py scrub|verify DISK [--needles FILE --names FILE]       (public-image-prep.sh --scrub DISK runs it)

Needs: python3, debugfs + e2fsck (e2fsprogs), losetup/lsblk (util-linux). Exit: 0 = PASS, 1 = findings, 2 = refused.
"""
import os
import re
import stat
import struct
import subprocess
import sys

CH = 4 << 20                         # bytes per raw read / write
HITS_MAX = 1_000_000                 # (block, needle) hits mapped at most (a dirty disk can hold far more)
PAGE = 4096                          # the swap header page (x86-64)
SWAP_SIG = b"SWAPSPACE2"
ESP_TYPE = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
REPO_TEXT = ("/opt/vibeos/os/", "/opt/vibeos/fs/system/")
SYSTEM_DIRS = ("/lib/", "/lib64/", "/bin/", "/sbin/", "/share/", "/src/", "/models", "/knowledge", "/dpkg", "/apt")
ENV = {**os.environ, "LC_ALL": "C", "PAGER": "__none__", "DEBUGFS_PAGER": "__none__"}


class Refused(Exception):
    pass


def say(msg: str):
    print(msg, flush=True)


def run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, env=ENV, **kw)


def dbg(spec: str, req: str, write: bool = False) -> str:
    """one debugfs request on the file system at `spec` (DISK?offset=N)"""
    r = run(["debugfs", *(["-w"] if write else []), "-R", req, spec])
    return r.stdout.decode("utf-8", "replace")


# ------------------------------------------------------------------------------------------------ the disk
def refuse_if_busy(disk: str):
    """a mounted / swapped-on / loop-attached disk is never touched"""
    st = os.stat(disk)
    if stat.S_ISREG(st.st_mode):
        r = run(["losetup", "-j", disk])
        if r.returncode == 0 and r.stdout.strip():
            raise Refused(f"{disk} is attached to a loop device ({r.stdout.decode().split(':')[0]}) — detach it first")
        return
    if not stat.S_ISBLK(st.st_mode):
        raise Refused(f"{disk} is neither an image file nor a block device")
    r = run(["lsblk", "-nrpo", "NAME,MOUNTPOINT", disk])
    for ln in r.stdout.decode().splitlines():
        name, _, mnt = ln.partition(" ")
        if mnt.strip():
            raise Refused(f"{name} is mounted at {mnt.strip()} — unmount every partition first")
    names = {ln.split()[0] for ln in r.stdout.decode().splitlines() if ln.split()}
    try:
        swaps = {ln.split()[0] for ln in open("/proc/swaps").read().splitlines()[1:] if ln.split()}
    except OSError:
        swaps = set()
    if names & swaps:
        raise Refused(f"{sorted(names & swaps)[0]} is in use as swap")


def gpt_volumes(f, size: int) -> list:
    """[(label, offset, length)] of the GPT's partitions (512-byte sectors), or None without a GPT"""
    f.seek(512)
    h = f.read(92)
    if len(h) < 92 or h[:8] != b"EFI PART":
        return None
    lba, n, esz = struct.unpack_from("<QII", h, 72)
    f.seek(lba * 512)
    raw = f.read(n * esz)
    out = []
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if len(e) < 128 or e[:16] == bytes(16):
            continue
        t = e[:16]
        tguid = "%08X-%04X-%04X-%s-%s" % (struct.unpack_from("<I", t)[0], struct.unpack_from("<H", t, 4)[0],
                                          struct.unpack_from("<H", t, 6)[0], t[8:10].hex().upper(), t[10:16].hex().upper())
        first, last = struct.unpack_from("<QQ", e, 32)
        name = e[56:128].decode("utf-16-le", "replace").rstrip("\x00")
        if 0 < first <= last and (last + 1) * 512 <= size:
            out.append({"label": f"p{i + 1}" + (f" ({name})" if name else ""), "off": first * 512,
                        "len": (last - first + 1) * 512, "esp": tguid == ESP_TYPE})
    return out


def fs_type(f, off: int) -> str:
    f.seek(off)
    b = f.read(1536)
    if len(b) >= 1082 and struct.unpack_from("<H", b, 1080)[0] == 0xEF53:
        return "ext4"
    if len(b) >= 512 and b[510:512] == b"\x55\xaa" and (b[54:59] in (b"FAT12", b"FAT16") or b[82:87] == b"FAT32"):
        return "vfat"
    return "other"


def volumes(disk: str) -> list:
    size = disk_size(disk)
    with open(disk, "rb") as f:
        vols = gpt_volumes(f, size)
        if vols is None:
            vols = [{"label": "the file system", "off": 0, "len": size, "esp": False}]
        for v in vols:
            v["fs"] = fs_type(f, v["off"])
    return vols


def disk_size(disk: str) -> int:
    with open(disk, "rb") as f:
        return f.seek(0, os.SEEK_END)


def zero_runs(fd, offs: list, unit: int, write: bool) -> int:
    """the units (byte offsets, each `unit` long) that hold a non-zero byte: zeroed when `write`; -> how many"""
    n, z = 0, bytes(unit)
    for o in offs:
        if os.pread(fd, unit, o) != z:
            n += 1
            if write:
                os.pwrite(fd, z, o)
    return n


def runs_of(blocks: list) -> list:
    """sorted block numbers -> [(first, count)] of consecutive runs"""
    out = []
    for b in sorted(blocks):
        if out and out[-1][0] + out[-1][1] == b:
            out[-1][1] += 1
        else:
            out.append([b, 1])
    return out


def zero_blocks(fd, base: int, bs: int, blocks: list, write: bool) -> int:
    """blocks (FS block numbers) holding non-zero bytes -> zeroed when `write`; read and written in CH chunks"""
    n = 0
    per = max(1, CH // bs)
    for first, cnt in runs_of(blocks):
        b = first
        while b < first + cnt:
            k = min(per, first + cnt - b)
            off = base + b * bs
            data = os.pread(fd, k * bs, off)
            if data.count(0) != len(data):
                for i in range(k):
                    if data[i * bs:(i + 1) * bs].count(0) != bs:
                        n += 1
                if write:
                    os.pwrite(fd, bytes(k * bs), off)
            b += k
    return n


# ------------------------------------------------------------------------------------------------ ext4
def ext4_info(spec: str) -> dict:
    s = dbg(spec, "stats")
    def field(name):
        m = re.search(rf"^{re.escape(name)}:\s*(.*)$", s, re.M)
        return m.group(1).strip() if m else ""
    return {"bs": int(field("Block size") or 0), "state": field("Filesystem state"),
            "features": field("Filesystem features").split(), "blocks": int(field("Block count") or 0)}


def ext4_clean(spec: str, info: dict) -> str:
    """'' when the FS may be scrubbed: clean, no journal to replay, e2fsck -fn finds nothing"""
    if info["bs"] not in (1024, 2048, 4096, 65536) or not info["blocks"]:
        return "not a readable ext4 file system"
    if info["state"] != "clean" or "needs_recovery" in info["features"]:
        return f"not clean (state '{info['state']}'{', its journal needs a replay' if 'needs_recovery' in info['features'] else ''}) — run e2fsck"
    r = run(["e2fsck", "-fn", spec])
    if r.returncode != 0:
        tail = r.stdout.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
        return f"e2fsck -fn reports problems (rc {r.returncode}: {tail[0][:120]})"
    return ""


def swapfiles(spec: str) -> list:
    """the file swap areas /etc/fstab names (absolute paths, not /dev/…)"""
    out = []
    for ln in dbg(spec, "cat /etc/fstab").splitlines():
        f = ln.split("#", 1)[0].split()
        if len(f) >= 3 and f[2] == "swap" and f[0].startswith("/") and not f[0].startswith("/dev/"):
            out.append(f[0])
    return out


def swap_extents(spec: str, path: str) -> list:
    """the DATA extents of `path` as [(logical, physical, length)] from its leaf extents (unwritten ones included) —
    never `blocks`, which also lists the extent tree's own index blocks; [] when it has none (missing, or not
    extent-mapped: then it is left alone)"""
    out = []
    leaf = re.compile(r"^\s*\d+/\s*\d+\s+\d+/\s*\d+\s+(\d+)\s*-\s*(\d+)\s+(\d+)\s*-\s*(\d+)\s+(\d+)", re.M)
    for m in leaf.finditer(dbg(spec, f'dump_extents -l "{path}"')):
        lo, hi, p0, p1, n = map(int, m.groups())
        if hi - lo + 1 == n == p1 - p0 + 1:
            out.append((lo, p0, n))
    return sorted(out)


def swap_header(page: bytes) -> bytes:
    """the header page mkswap writes for this swap area: zeros + version, last_page, nr_badpages, UUID, label (+ the bad
    page list) + the signature — None when `page` is no swap header"""
    if len(page) != PAGE or page[PAGE - 10:] != SWAP_SIG:
        return None
    version, last, nbad = struct.unpack_from("<III", page, 1024)
    if version != 1 or nbad > (PAGE - 10 - 1536) // 4:
        return None
    new = bytearray(PAGE)
    new[1024:1024 + 12 + 16 + 16] = page[1024:1024 + 44]
    new[1536:1536 + 4 * nbad] = page[1536:1536 + 4 * nbad]
    new[PAGE - 10:] = SWAP_SIG
    return bytes(new)


def scrub_swap(fd, base: int, spec: str, info: dict, write: bool, owned: dict = None) -> list:
    """-> [(path, pages_nonzero_beyond_header, header_fixed, problem)]; `owned` gets {physical block: path} of every
    swapfile data block (the needle mapping then needs no debugfs call for them)"""
    out = []
    for path in swapfiles(spec):
        ext = swap_extents(spec, path)
        if not ext:
            out.append((path, 0, False, "named in /etc/fstab but not an extent-mapped file here — not scrubbed"))
            continue
        bs = info["bs"]
        if bs != PAGE:
            out.append((path, 0, False, f"block size {bs} (the swap header page is {PAGE}) — not scrubbed"))
            continue
        if ext[0][0] != 0:
            out.append((path, 0, False, "no header page at logical block 0"))
            continue
        h0 = ext[0][1]
        blocks = [p + i for lo, p, n in ext for i in range(n) if not (lo == 0 and i == 0)]
        if owned is not None:
            owned[h0] = path
            owned.update((b, path) for b in blocks)
        page = os.pread(fd, PAGE, base + h0 * bs)
        new = swap_header(page)
        if new is None:
            out.append((path, 0, False, "its first page is no swap header (SWAPSPACE2) — not scrubbed"))
            continue
        fixed = new != page
        if fixed and write:
            os.pwrite(fd, new, base + h0 * bs)
        n = zero_blocks(fd, base, bs, blocks, write)
        out.append((path, n, fixed, ""))
    return out


def unused_blocks(spec: str, bs: int) -> list:
    """the free blocks holding a non-zero byte (debugfs dump_unused streams each one: a header line + its bytes, which
    are read here and never printed)"""
    p = subprocess.Popen(["debugfs", "-R", "dump_unused", spec], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV)
    out = []
    head = re.compile(rb"\nUnused block (\d+) contains non-zero data:\n\n")
    buf, pos = b"", 0          # a read offset, never a slice per block: GBs of free data stay linear
    while True:
        chunk = p.stdout.read(4 << 20)
        if chunk:
            buf = buf[pos:] + chunk
            pos = 0
        while True:
            m = head.match(buf, pos)
            if not m or len(buf) < m.end() + bs:
                break
            out.append(int(m.group(1)))
            pos = m.end() + bs
        if not chunk:
            break
    p.wait()
    if buf[pos:].strip():
        raise RuntimeError(f"debugfs dump_unused: unexpected output ({len(buf) - pos} bytes left)")
    return out


def scrub_ext4(fd, v: dict, disk: str, write: bool, report: dict):
    spec = f"{disk}?offset={v['off']}"
    info = ext4_info(spec)
    v["bs"] = info["bs"]
    why = ext4_clean(spec, info)
    if why:
        if write:
            raise Refused(f"{v['label']}: ext4 {why}")
        report["fail"].append(f"{v['label']}: ext4 {why} — its free blocks cannot be judged")
        return
    v["swap"] = {}
    for path, n, fixed, problem in scrub_swap(fd, v["off"], spec, info, write, v["swap"]):
        if problem:
            report["fail" if not write else "warn"].append(f"{v['label']}: swapfile {path}: {problem}")
        elif write:
            say(f"  {v['label']}: swapfile {path}: {n} old page(s) zeroed, header {'rebuilt (same UUID/label)' if fixed else 'kept (as mkswap writes it)'}")
        elif n or fixed:
            report["fail"].append(f"{v['label']}: swapfile {path}: {n} page(s) beyond its header hold old data"
                                  + (", the header page holds stray bytes" if fixed else ""))
        else:
            say(f"  {v['label']}: swapfile {path}: clean (only its header)")
    if write:
        os.fsync(fd)
    free = unused_blocks(spec, info["bs"])
    v["free_nz"] = set(free)                # the needle mapping: a hit in one of these is a FREE block
    n = zero_blocks(fd, v["off"], info["bs"], free, write) if free else 0
    if write:
        os.fsync(fd)
        say(f"  {v['label']}: ext4: {n} non-zero free block(s) zeroed ({n * info['bs']} B)")
        why = ext4_clean(spec, ext4_info(spec))
        if why:
            report["fail"].append(f"{v['label']}: ext4 after the scrub: {why}")
    elif n:
        report["fail"].append(f"{v['label']}: ext4: {n} free block(s) hold old data")
    else:
        say(f"  {v['label']}: ext4: every free block is zero")


# ------------------------------------------------------------------------------------------------ FAT (the ESP)
def fat_layout(fd, off: int) -> dict:
    bpb = os.pread(fd, 512, off)
    bps, spc, rsv, nfats, rootents, tot16, _media, fsz16 = struct.unpack_from("<HBHBHHBH", bpb, 11)
    tot32, = struct.unpack_from("<I", bpb, 32)
    fsz32, = struct.unpack_from("<I", bpb, 36)
    fsz = fsz16 or fsz32
    tot = tot16 or tot32
    if not (bps in (512, 1024, 2048, 4096) and spc and nfats and fsz and tot):
        raise RuntimeError("not a FAT boot sector")
    rootsec = (rootents * 32 + bps - 1) // bps
    data = rsv + nfats * fsz + rootsec
    nclus = (tot - data) // spc
    kind = 12 if nclus < 4085 else 16 if nclus < 65525 else 32
    return {"bps": bps, "spc": spc, "rsv": rsv, "nfats": nfats, "fsz": fsz, "data": data, "nclus": nclus, "kind": kind,
            "clus": bps * spc, "off": off}


def fat_entries(fd, L: dict, copy: int) -> list:
    raw = os.pread(fd, L["fsz"] * L["bps"], L["off"] + (L["rsv"] + copy * L["fsz"]) * L["bps"])
    n = L["nclus"] + 2
    if L["kind"] == 32:
        return [struct.unpack_from("<I", raw, 4 * i)[0] & 0x0FFFFFFF for i in range(n)]
    if L["kind"] == 16:
        return [struct.unpack_from("<H", raw, 2 * i)[0] for i in range(n)]
    out = []
    for i in range(n):
        v = struct.unpack_from("<H", raw, i + i // 2)[0]
        out.append(v >> 4 if i & 1 else v & 0xFFF)
    return out


def free_clusters(fd, L: dict) -> list:
    tables = [fat_entries(fd, L, c) for c in range(L["nfats"])]
    return [c for c in range(2, L["nclus"] + 2) if all(t[c] == 0 for t in tables)]


def scrub_fat(fd, v: dict, write: bool, report: dict):
    try:
        L = fat_layout(fd, v["off"])
    except RuntimeError as e:
        report["fail"].append(f"{v['label']}: vfat: {e}")
        return
    base = v["off"] + L["data"] * L["bps"]
    offs = [base + (c - 2) * L["clus"] for c in free_clusters(fd, L)]
    n = zero_runs(fd, offs, L["clus"], write)
    if write:
        os.fsync(fd)
        say(f"  {v['label']}: vfat (FAT{L['kind']}): {n} non-zero free cluster(s) zeroed ({n * L['clus']} B)")
    elif n:
        report["fail"].append(f"{v['label']}: vfat: {n} free cluster(s) hold old data")
    else:
        say(f"  {v['label']}: vfat: every free cluster is zero")
    v["fat"] = L


# ------------------------------------------------------------------------------------------------ the needles
def needle_scan(disk: str, vols: list, needles: str, names: list, report: dict):
    """the raw disk searched for the needles; each (block, needle) hit mapped to where it sits — never printing a needle"""
    with open(needles, "rb") as f:
        pats = [ln.rstrip(b"\n") for ln in f]
    # the unit a hit is judged by — an ext4 block, a FAT cluster, 4 KiB elsewhere — so a disk full of hits (a swapfile of
    # old pages) costs one counter per unit, never one record per hit
    for v in vols:
        if v["fs"] == "ext4" and not v.get("bs"):
            v["bs"] = ext4_info(f"{disk}?offset={v['off']}")["bs"] or 4096
        elif v["fs"] == "vfat" and not v.get("fat"):
            try:
                with open(disk, "rb") as f:
                    v["fat"] = fat_layout(f.fileno(), v["off"])
            except RuntimeError:
                pass
        v["unit"] = v.get("bs") if v["fs"] == "ext4" else (v["fat"]["bps"] if v.get("fat") else 4096)
    # the raw bytes are read here, in chunks with an overlap: `grep -abo` holds a whole newline-free "line" in memory (GBs
    # of zeros), and GNU grep 3.12's -z offsets are wrong on a sparse image. Each needle counts at most once per unit, and
    # the search jumps to the next unit after a hit: a swapfile full of old pages costs one entry per block, not per hit.
    hits, total, size = {}, 0, disk_size(disk)
    maxlen = max(len(n) for n in pats)
    unit_of = [(v["off"], v["off"] + v["len"], v["unit"]) for v in vols]

    def unit(off):
        for i, (a, b, u) in enumerate(unit_of):
            if a <= off < b:
                return i, a + (off - a) // u * u, u
        return None, off // 4096 * 4096, 4096
    full = False
    with open(disk, "rb") as f:
        pos, tail = 0, b""
        while pos < size and not full:
            chunk = f.read(64 << 20)
            if not chunk:
                break
            buf, base = tail + chunk, pos - len(tail)
            if buf.count(0) != len(buf):
                for idx, n in enumerate(pats):
                    if not n:
                        continue
                    i = buf.find(n)
                    while i >= 0:
                        vi, ustart, u = unit(base + i)
                        key = (vi, ustart, names[idx] if idx < len(names) else "a needle")
                        if key not in hits:
                            hits[key] = 1
                            total += 1
                            if total >= HITS_MAX:
                                full = True
                                break
                        i = buf.find(n, max(i + 1, ustart + u - base))
                    if full:
                        break
            pos += len(chunk)
            tail = buf[-(maxlen - 1):] if maxlen > 1 else b""
    if full:
        report["fail"].append(f"raw bytes: needles in more than {HITS_MAX} blocks — the mapping below stopped counting there")
    where = {}
    by_vol = {}
    for (vi, off, name), k in hits.items():
        by_vol.setdefault(vi, (vols[vi] if vi is not None else None, []))[1].append((off, name, k))
    for key, (v, hs) in by_vol.items():
        if v is None:
            for off, name, k in hs:
                w = (name, "outside the partitions (GPT / gaps)", "fail")
                where[w] = where.get(w, 0) + k
            continue
        if v["fs"] == "ext4":
            spec = f"{disk}?offset={v['off']}"
            bs = v.get("bs") or ext4_info(spec)["bs"]
            swap, free_nz = v.get("swap") or {}, v.get("free_nz") or set()
            blocks = sorted({(off - v["off"]) // bs for off, _, _ in hs} - set(swap) - free_nz)
            owner, path = {}, {}
            for i in range(0, len(blocks), 20000):          # icheck walks every inode per call: few, large calls
                for ln in dbg(spec, "icheck " + " ".join(map(str, blocks[i:i + 20000]))).splitlines()[1:]:
                    f = ln.split(None, 1)
                    if len(f) == 2 and f[0].isdigit():
                        owner[int(f[0])] = f[1].strip()
            inodes = sorted({o for o in owner.values() if o.isdigit()}, key=int)
            for i in range(0, len(inodes), 2000):
                for ln in dbg(spec, "ncheck " + " ".join(inodes[i:i + 2000])).splitlines()[1:]:
                    f = ln.split(None, 1)
                    if len(f) == 2 and f[0].isdigit():
                        path.setdefault(f[0], re.sub(r"/+", "/", f[1].strip()))     # debugfs writes //swapfile
            isdir = {o: "Type: directory" in dbg(spec, f"stat <{o}>")[:300] for o in inodes[:500]}
            for off, name, k in hs:
                b = (off - v["off"]) // bs
                o = owner.get(b, "")
                if b in swap:
                    loc, lvl = f"{v['label']}: swapfile {swap[b]} (old memory pages)", "fail"
                elif b in free_nz:
                    loc, lvl = f"{v['label']}: a FREE block", "fail"
                elif o == "8":
                    loc, lvl = f"{v['label']}: the ext4 journal (old metadata copies)", "fail"
                elif o.isdigit():
                    p_ = path.get(o, f"inode {o}")
                    if p_.startswith(REPO_TEXT):
                        loc, lvl = f"{v['label']}: shipped repo text ({next(r for r in REPO_TEXT if p_.startswith(r))}…)", "warn"
                    elif isdir.get(o):
                        system = any(x in p_ + "/" for x in SYSTEM_DIRS)
                        loc, lvl = f"{v['label']}: a name in directory {p_}", "warn" if system else "fail"
                    else:
                        loc, lvl = f"{v['label']}: inside the file {p_} (the file verify judges files)", "warn"
                else:      # owned by no inode: metadata — or free, when the structure pass could not list the free blocks
                    loc, lvl = (f"{v['label']}: a metadata block (not a file)" if "free_nz" in v
                                else f"{v['label']}: a block outside every file (free or metadata)"), "fail"
                where[(name, loc, lvl)] = where.get((name, loc, lvl), 0) + k
        elif v["fs"] == "vfat":
            with open(disk, "rb") as f:
                L = v.get("fat") or fat_layout(f.fileno(), v["off"])
                free = set(free_clusters(f.fileno(), L))
            base = v["off"] + L["data"] * L["bps"]
            for off, name, k in hs:
                if off < base:
                    loc, lvl = f"{v['label']}: FAT metadata (boot sector, FAT or root directory)", "fail"
                else:
                    c = (off - base) // L["clus"] + 2
                    loc, lvl = ((f"{v['label']}: a FREE cluster", "fail") if c in free
                                else (f"{v['label']}: inside a file or directory of the ESP (the file verify judges files)", "warn"))
                where[(name, loc, lvl)] = where.get((name, loc, lvl), 0) + k
        else:
            for off, name, k in hs:
                key2 = (name, f"{v['label']}: a partition of another type", "fail")
                where[key2] = where.get(key2, 0) + k
    for (name, loc, lvl), k in sorted(where.items()):
        report[lvl].append(f"raw bytes: {name} in {k} block(s) — {loc}")
    say(f"  needles: {len(pats)} searched in {size} raw bytes, found in {total} block(s) / cluster(s)")


# ------------------------------------------------------------------------------------------------ main
def main(argv) -> int:
    if len(argv) < 2 or argv[0] not in ("scrub", "verify"):
        print(__doc__.split("\n\n", 1)[0], file=sys.stderr)
        return 2
    mode, disk = argv[0], argv[1]
    needles = names_file = None
    rest = argv[2:]
    while rest:
        if rest[0] == "--needles" and len(rest) > 1:
            needles, rest = rest[1], rest[2:]
        elif rest[0] == "--names" and len(rest) > 1:
            names_file, rest = rest[1], rest[2:]
        else:
            print(f"public-image-scrub: unknown argument {rest[0]}", file=sys.stderr)
            return 2
    write = mode == "scrub"
    report = {"fail": [], "warn": []}
    try:
        if not os.path.exists(disk):
            raise Refused(f"{disk} does not exist")
        for tool in ("debugfs", "e2fsck"):
            if run(["sh", "-c", f"command -v {tool}"]).returncode != 0:
                raise Refused(f"{tool} is not installed (e2fsprogs)")
        refuse_if_busy(disk)
        vols = volumes(disk)
        say(f"{'scrub' if write else 'verify'} {disk}: {disk_size(disk)} B, "
            + ", ".join(f"{v['label']} {v['fs']}" for v in vols))
        fd = os.open(disk, os.O_RDWR if write else os.O_RDONLY)
        try:
            if write:          # every ext4 must be scrubbable before anything is written
                for v in vols:
                    if v["fs"] == "ext4":
                        spec = f"{disk}?offset={v['off']}"
                        why = ext4_clean(spec, ext4_info(spec))
                        if why:
                            raise Refused(f"{v['label']}: ext4 {why}")
            for v in vols:
                if v["fs"] == "ext4":
                    scrub_ext4(fd, v, disk, write, report)
                elif v["fs"] == "vfat":
                    scrub_fat(fd, v, write, report)
                else:
                    say(f"  {v['label']}: no ext4 / FAT file system — left as it is")
        finally:
            os.close(fd)
        if write:              # the scrub ends with the read-only verify of what it wrote
            say("== raw verify ==")
            fd = os.open(disk, os.O_RDONLY)
            try:
                for v in vols:
                    if v["fs"] == "ext4":
                        scrub_ext4(fd, v, disk, False, report)
                    elif v["fs"] == "vfat":
                        scrub_fat(fd, v, False, report)
            finally:
                os.close(fd)
        if needles and os.path.getsize(needles):
            names = [ln.strip() for ln in open(names_file)] if names_file else []
            needle_scan(disk, vols, needles, names, report)
        else:
            report["warn"].append("no private patterns: the raw bytes were not searched for the operator's strings")
    except Refused as e:
        say(f"public-image-scrub: refused — {e}")
        return 2
    for w in report["warn"]:
        say(f"WARN  {w}")
    for f in report["fail"]:
        say(f"FAIL  {f}")
    if report["fail"]:
        say(f"RAW VERIFY: FAIL — {len(report['fail'])} finding(s), {len(report['warn'])} warning(s)")
        return 1
    say(f"RAW VERIFY: PASS — 0 findings, {len(report['warn'])} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
