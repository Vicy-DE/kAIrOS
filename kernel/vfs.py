"""vibeOS virtual filesystem — Android-style partition layout.

  /system   write-protected: OS identity, manifest, API docs, prompts (repopulated at boot)
  /data     read-write: app + user files; any app may format it via the API ("factory reset")

All paths arriving from apps are virtual ("/data/notes/todo.txt") and resolved strictly
inside the backing directories — no traversal, no symlink escape.
"""
import json
import shutil
import time
from pathlib import Path


class ReadOnlyError(Exception):
    pass


class VFS:
    def __init__(self, vibeos_dir: Path):
        self.root = vibeos_dir / "fs"
        self.parts = {"system": self.root / "system", "data": self.root / "data"}
        for p in self.parts.values():
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as e:   # module-level in the kernel: a bad disk must not crash-loop it
                print(f"[vfs] WARN cannot create {p}: {e}", flush=True)

    # ---------------- path safety ----------------

    def resolve(self, vpath: str, write: bool = False) -> Path:
        vpath = "/" + vpath.strip().lstrip("/")
        parts = [p for p in vpath.split("/") if p not in ("", ".")]
        if not parts or parts[0] not in self.parts:
            raise FileNotFoundError(f"unknown partition in '{vpath}' — use /system or /data")
        if ".." in parts:
            raise PermissionError("path traversal is not allowed")
        if write and parts[0] == "system":
            raise ReadOnlyError("/system is write-protected (read-only partition)")
        base = self.parts[parts[0]]
        target = base.joinpath(*parts[1:]) if len(parts) > 1 else base
        if not str(target.resolve()).startswith(str(base.resolve())):
            raise PermissionError("path escapes its partition")
        return target

    # ---------------- operations ----------------

    def list(self, vpath: str = "/"):
        if vpath.strip("/") == "":
            return [{"name": n, "type": "dir", "size": 0} for n in self.parts]
        d = self.resolve(vpath)
        if not d.exists():
            raise FileNotFoundError(vpath)
        if d.is_file():
            return [self._entry(d)]
        return sorted((self._entry(c) for c in d.iterdir()),
                      key=lambda e: (e["type"] != "dir", e["name"].lower()))

    @staticmethod
    def _entry(p: Path):
        return {"name": p.name, "type": "dir" if p.is_dir() else "file",
                "size": p.stat().st_size if p.is_file() else 0,
                "modified": int(p.stat().st_mtime)}

    def read(self, vpath: str) -> str:
        f = self.resolve(vpath)
        if not f.is_file():
            raise FileNotFoundError(vpath)
        if f.stat().st_size > 2 * 1024 * 1024:
            raise PermissionError("file too large for the API (2 MB limit)")
        return f.read_text(errors="replace")

    def write(self, vpath: str, content: str):
        f = self.resolve(vpath, write=True)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        return self._entry(f)

    def mkdir(self, vpath: str):
        d = self.resolve(vpath, write=True)
        d.mkdir(parents=True, exist_ok=True)
        return self._entry(d)

    def delete(self, vpath: str):
        t = self.resolve(vpath, write=True)
        if t == self.parts["data"]:
            raise PermissionError("cannot delete the partition itself — use format")
        if t.is_dir():
            shutil.rmtree(t)
        elif t.exists():
            t.unlink()
        else:
            raise FileNotFoundError(vpath)
        return True

    def format_data(self):
        """Factory-reset the /data partition. Any app may invoke this via the API."""
        data = self.parts["data"]
        for child in data.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        (data / ".formatted").write_text(json.dumps({"at": time.time()}))
        return True

    # ---------------- /system population (each boot) ----------------

    def populate_system(self, files: dict):
        """files: {virtual_relpath: source Path or literal str}"""
        sysroot = self.parts["system"]
        failed = 0
        for rel, src in files.items():
            dst = sysroot / rel
            try:   # runs at every kernel start: a full / read-only disk must not crash-loop it
                dst.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(src, Path):
                    if src.is_file():
                        shutil.copy2(src, dst)
                else:
                    dst.write_text(src)
            except OSError as e:
                failed += 1
                if failed == 1:
                    print(f"[vfs] WARN /system not (fully) populated: {rel}: {e}", flush=True)
        return failed
