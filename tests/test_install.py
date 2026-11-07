"""Installing and updating Ollama: a failed download must never remove a working install."""
import hashlib, io, os, shutil, subprocess, sys, tarfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from _util import load, ok
if not shutil.which("zstd"):
    print("skipped: zstd not installed"); sys.exit(0)
gy = load()
gy.log = lambda m: None

def make_pkg(files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for name, data, mode in files:
            ti = tarfile.TarInfo(name); ti.size = len(data); ti.mode = mode; t.addfile(ti, io.BytesIO(data))
    return subprocess.run(["zstd", "-q", "-c"], input=raw.getvalue(), capture_output=True, check=True).stdout
def ollama_pkg(ver):
    return make_pkg([("bin/ollama", f"#!/bin/sh\necho 'ollama version is {ver}'\n".encode(), 0o755),
                     ("lib/ollama/libggml-base.so", b"x", 0o644)])
ROCM = make_pkg([("lib/ollama/rocm/librocblas.so", b"r", 0o644)])
serve = {}
sums_override = {}
def checksums():
    lines = [f"{sums_override.get(n) or hashlib.sha256(d).hexdigest()}  ./{n}" for n, (d, _) in serve.items()
             if sums_override.get(n) != "missing"]
    return ("\n".join(lines) + "\n").encode()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        name = self.path.rsplit("/", 1)[-1]
        mode = (checksums(), False) if name == "sha256sum.txt" else serve.get(name)
        if mode is None: self.send_response(404); self.end_headers(); return
        data, trunc = mode
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data[: len(data)//2] if trunc else data)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
gy.ollama_download_base = lambda: f"http://127.0.0.1:{srv.server_port}/download/v0.41.0"

def run(label, expect_exit):
    try:
        gy.install_ollama("rocm"); code = 0
    except SystemExit as e: code = e.code
    ver = subprocess.run([str(gy.OLLAMA_BIN), "--version"], capture_output=True, text=True).stdout.strip() if gy.OLLAMA_BIN.exists() else "MISSING"
    leftovers = sorted(p.name for p in gy.OLLAMA_DIR.parent.iterdir())
    ok(f"{label}: installed '{ver.split()[-1]}'")
    assert (code != 0) == expect_exit
    return ver

serve.update({"ollama-linux-amd64.tar.zst": (ollama_pkg("0.40.10"), False), "ollama-linux-amd64-rocm.tar.zst": (ROCM, False)})
assert run("fresh install", False).endswith("0.40.10")
assert (gy.OLLAMA_DIR / "lib/ollama/rocm/librocblas.so").exists() and os.readlink(gy.BIN_DIR / "ollama") == str(gy.OLLAMA_BIN)
serve["ollama-linux-amd64.tar.zst"] = (ollama_pkg("0.41.0"), False); serve.pop("ollama-linux-amd64-rocm.tar.zst")
assert run("rocm package 404", True).endswith("0.40.10")
serve["ollama-linux-amd64-rocm.tar.zst"] = (ROCM, True)
assert run("rocm truncated", True).endswith("0.40.10")
serve["ollama-linux-amd64.tar.zst"] = (b"not a zstd archive", False); serve["ollama-linux-amd64-rocm.tar.zst"] = (ROCM, False)
assert run("corrupt archive", True).endswith("0.40.10")
serve["ollama-linux-amd64.tar.zst"] = (ollama_pkg("0.41.0"), False)
sums_override["ollama-linux-amd64.tar.zst"] = "0" * 64        # altered on the way
assert run("checksum mismatch", True).endswith("0.40.10")
sums_override["ollama-linux-amd64.tar.zst"] = "missing"
assert run("no published checksum", True).endswith("0.40.10")
sums_override.clear()
(gy.OLLAMA_DIR / "lib/ollama/stale-from-old-version.so").write_bytes(b"old")
assert run("good update", False).endswith("0.41.0")
assert not (gy.OLLAMA_DIR / "lib/ollama/stale-from-old-version.so").exists(), "old libraries leaked into new install"
assert not list(gy.CACHE_DIR.glob("*.zst")), "archives left in cache"
print("install tests passed")
