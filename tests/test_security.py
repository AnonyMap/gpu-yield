"""1.10.1: the security audit, the bug review and the performance review, one check each."""
import contextlib
import http.client
import io
import json
import os
import resource
import socket
import threading
import time
import types
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _util import free_port, load, ok, wait

gy = load()
gy.os.geteuid = lambda: 1000
calls = []
gy.user_systemctl = lambda *a: calls.append(a) or types.SimpleNamespace(returncode=0, stdout="", stderr="")
gy.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
gy.write_private(gy.CONFIG_FILE, gy.CONFIG_TEMPLATE)


def attempt(fn, *a, **k):
    try:
        fn(*a, **k)
    except SystemExit as e:
        return e.code or 0
    return 0


# --- stand-in: a request body is thrown away as it arrives, never kept
standin = gy.StandIn(free_port())
assert standin.start()
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
size = 300 * 65536                            # ~20 MB, declared honestly
with socket.create_connection(("127.0.0.1", standin.port)) as c:
    c.sendall(f"POST /api/chat HTTP/1.1\r\nHost: x\r\nContent-Length: {size}\r\n\r\n".encode())
    chunk = b"x" * 65536
    for _ in range(300):
        c.sendall(chunk)
    c.settimeout(10)
    reply = c.recv(200)
grown = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
assert reply.startswith(b"HTTP/1.1 503"), reply
assert grown < 10_000, f"memory grew {grown} KiB for a 20 MB body"
with socket.create_connection(("127.0.0.1", standin.port)) as c:   # Content-Length: -1
    c.sendall(b"POST /api/chat HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n" + b"x" * 65536)
    c.settimeout(5)
    try:
        assert c.recv(200).startswith(b"HTTP/1.1 503")
    except ConnectionResetError:
        pass                                  # answered and hung up without reading: fine
code = http.client.HTTPConnection("127.0.0.1", standin.port, timeout=5)
code.request("POST", "/v1/chat/completions", body=b"{}")
assert code.getresponse().status == 503
t = time.monotonic()
standin.stop()
assert time.monotonic() - t < 2
ok("stand-in: a body of any size (or Content-Length: -1) is drained, not stored; stops at once")

# --- remote status: only a small, sane gpu-yield status, printed without escape codes
class Remote(BaseHTTPRequestHandler):
    body = b"{}"
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(Remote.body)))
        self.end_headers()
        self.wfile.write(Remote.body)
rport = free_port()
srv = ThreadingHTTPServer(("127.0.0.1", rport), Remote)
threading.Thread(target=srv.serve_forever, daemon=True).start()

evil = {"state": "ai", "host": "pc\x1b]0;pwned\x07", "model": "m\x1b[2J", "mac": "not-a-mac",
        "events": [{"at": time.time(), "text": "hi\x1b]52;c;ZXZpbA==\x07there"}], "since": time.time()}
Remote.body = json.dumps(evil).encode()
st = gy.fetch_remote_status("127.0.0.1", rport)
assert "\x1b" not in json.dumps(st, ensure_ascii=False) and st["mac"] is None
assert st["host"] == "pc]0;pwned" and st["events"][0]["text"] == "hi]52;c;ZXZpbA==there"
out = io.StringIO()
with contextlib.redirect_stdout(out):
    gy.cmd_status(types.SimpleNamespace(host=f"127.0.0.1:{rport}"))
assert "\x1b" not in out.getvalue() and "\x07" not in out.getvalue()
for body in (b"[1, 2]", b'"text"', b'{"unrelated": 1}', b"x" * (gy.REMOTE_MAX + 10)):
    Remote.body = body
    try:
        gy.fetch_remote_status("127.0.0.1", rport)
        raise AssertionError(f"accepted {body[:20]!r}")
    except ValueError:
        pass
Remote.body = json.dumps({"state": "ai", "since": "yesterday"}).encode()
assert attempt(gy.cmd_status, types.SimpleNamespace(host=f"127.0.0.1:{rport}")) == 1
srv.shutdown()
ssh = socket.socket()
ssh.bind(("127.0.0.1", 0)); ssh.listen()
def banner():
    conn, _ = ssh.accept()
    conn.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
    conn.close()
threading.Thread(target=banner, daemon=True).start()
try:
    gy.fetch_remote_status("127.0.0.1", ssh.getsockname()[1])
    raise AssertionError("accepted an SSH banner")
except ValueError:
    pass
ok("remote status: escape codes stripped, size and shape checked, bad MACs dropped, no tracebacks")

# --- secrets: never printed in errors, entered at a prompt or generated
problem = gy.config_problem(dict(gy.DEFAULTS, control_password=31415926))
assert "31415926" not in problem and "secret" in problem
gy.sys.stdin = io.StringIO("")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    gy.cmd_config(types.SimpleNamespace(key="control_password", value="-"))
pw = gy.load_config()["control_password"]
assert len(pw) >= 20 and pw in out.getvalue(), "an empty answer generates a strong password"
gy.sys.stdin = io.StringIO("correct-horse-🐎\n")
with contextlib.redirect_stdout(io.StringIO()):
    gy.cmd_config(types.SimpleNamespace(key="control_password", value="-"))
assert gy.load_config()["control_password"] == "correct-horse-🐎", "emoji must save"
ok("secrets: hidden from error messages; `config control_password -` prompts or generates; emoji save")

# --- status page: not readable or frameable by websites; the host hint is worked out once
class W:
    cfg = dict(gy.DEFAULTS)
    def snapshot(self, now=None, memory=True): return {"state": "ai", "memory": None}
    def api_models(self): return {}
pport = free_port()
page = gy.StatusServer(pport, W())
assert page.start()
c = http.client.HTTPConnection("127.0.0.1", pport, timeout=3)
c.request("GET", "/status")
r = c.getresponse(); r.read()
assert r.getheader("Access-Control-Allow-Origin") is None and r.getheader("X-Frame-Options") == "DENY"
lan = []
gy.lan_address = lambda: lan.append(1) or ("192.168.1.9", "192.168.1.0/24")
for _ in range(20):
    c = http.client.HTTPConnection("127.0.0.1", pport, timeout=3)
    c.request("GET", "/api/models", headers={"Host": "evil.example"})
    r = c.getresponse(); r.read()
    assert r.status == 421
assert len(lan) == 1, "the hint must not run `ip` per request"
page.stop()
ok("status page: no CORS, no framing; refused names don't make it run `ip` each time")

# --- config error: your own Ollama unit is stopped too
gy.CONFIG_FILE.write_text('ollama_unit = "ollama.service"\nport = "oops"\n')
calls.clear()
gy.StandIn.start = lambda self: True
th = threading.Thread(target=gy.load_config_or_wait, daemon=True)
th.start()
wait(lambda: ("stop", "ollama.service") in calls, msg="custom unit stopped")
gy.write_private(gy.CONFIG_FILE, gy.CONFIG_TEMPLATE)
ok("a broken config stops a custom Ollama unit as well as gpu-yield's own")

# --- ROCm chosen but not installed: the unit keeps using Vulkan
cfg = dict(gy.load_config(), backend="rocm")
(gy.OLLAMA_DIR / "lib/ollama").mkdir(parents=True, exist_ok=True)
assert gy.backend_installed(cfg) is False and 'OLLAMA_VULKAN=1' in gy.ollama_unit_text(cfg)
(gy.OLLAMA_DIR / "lib/ollama/rocm").mkdir()
assert gy.backend_installed(cfg) is True and "OLLAMA_VULKAN" not in gy.ollama_unit_text(cfg)
ok("backend rocm without the ROCm download keeps Ollama on Vulkan, not the CPU")

# --- setup: a new model is saved only after it downloads
gy.change_settings({"model": "gpt-oss:20b"})
gy.check_conflicts = lambda args: None
gy.OLLAMA_BIN.parent.mkdir(parents=True, exist_ok=True)
gy.OLLAMA_BIN.write_text("")
gy.enable_linger = gy.enable_wol = gy.open_firewall = lambda *a: None
gy.write_unit = lambda *a: False
gy.ensure_ollama_unit = lambda cfg: False
gy.Ollama.wait_ready = lambda self, *a: True
pulled = []
gy.pull_and_load = lambda cfg, name: pulled.append(name) or False       # the download fails
args = types.SimpleNamespace(model="qwen3.5:9bb", context=None, backend=None, reinstall=False,
                             yes=True, skip_pull=False, no_firewall=True)
with contextlib.redirect_stdout(io.StringIO()):
    assert attempt(gy.cmd_setup, args) == 1
assert pulled == ["qwen3.5:9bb"] and gy.load_config()["model"] == "gpt-oss:20b"
gy.pull_and_load = lambda cfg, name: pulled.append(name) or True
with contextlib.redirect_stdout(io.StringIO()):
    assert attempt(gy.cmd_setup, args) == 0
assert gy.load_config()["model"] == "qwen3.5:9bb"
ok("setup --model: a failed download keeps the working model; a good one is saved")

# --- restarts: another load ends a restart's hold, so out-of-memory can still restart
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="m"))
w.state = "ai"
w.restart_gen = w.warm_gen = 5
w.warming = True
assert w.restart_in_progress()
w.warm_gen = 6                                  # a page change started a different load
assert not w.restart_in_progress()
w.oom_retried = False
w.out_of_memory(6, "m", "out of memory")
assert w.restart_request, "the automatic restart must not be lost"
w.restart_request, w.oom_retried, w.restart_busy = None, False, True
w.out_of_memory(6, "m", "out of memory")
assert w.warm_status.startswith("failed"), "with a restart already running, the load still ends"
ok("restarts: a different load ends a restart's hold; out-of-memory is never left hanging")

# --- performance: empty command lines are skipped after the first look; status writes
gy._NO_CMDLINE.clear()
gy.scan_games(gy.DEFAULTS)
cached = len(gy._NO_CMDLINE)
reads = []
real = gy._read_cmdline
gy._read_cmdline = lambda pid, full=False: reads.append(pid) or real(pid, full)
gy.scan_games(gy.DEFAULTS)
gy._read_cmdline = real
assert cached > 0 and not set(reads) & {pid for pid, _ in gy._NO_CMDLINE}, "re-read a cached process"
ok(f"scan skips the {cached} processes with no command line after the first look")

w = gy.Watcher(dict(gy.DEFAULTS, port=1))
w.state = "ai"
writes = []
gy.write_status_file = lambda st: writes.append(st)
n = 0
gy.memory_readings = lambda: {"ram_used": time.time_ns()}      # changes on every read
for i in range(20):
    w.write_status(100.0 + i * 0.1)
assert len(writes) == 1 and writes[0]["memory"], len(writes)
w.write_status(111.0)
assert len(writes) == 2, "the 10 s heartbeat still writes"
ok("memory readings alone don't rewrite the status file every poll; it still has them")
print("security and review-fix tests passed")
