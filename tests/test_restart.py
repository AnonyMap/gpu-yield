"""Restarting the AI: free GPU memory, name what else holds some, reload; automatic once
on running out of GPU memory; the page button and `gpu-yield restart`."""
import io
import json
import os
import signal
import tempfile
import threading
import types
import urllib.error
from pathlib import Path

from _util import load, ok, wait

gy = load()
gy.os.geteuid = lambda: 1000
gy.user_systemctl = lambda *a: types.SimpleNamespace(returncode=0, stdout="", stderr="")

# --- reading the driver's per-program accounting
old_kernel = "pos:\t0\ndrm-driver:\tamdgpu\ndrm-client-id:\t7\ndrm-memory-vram:\t921600 KiB\ndrm-memory-gtt:\t2048 KiB\n"
new_kernel = "drm-driver:\tamdgpu\ndrm-client-id:\t9\ndrm-total-vram:\t2 GiB\ndrm-resident-vram:\t1536 MiB\n"
old_kernel = old_kernel.replace("drm-driver", "drm-pdev:\t0000:03:00.0\ndrm-driver")
new_kernel = new_kernel.replace("drm-driver", "drm-pdev:\t0000:03:00.0\ndrm-driver")
assert gy.fdinfo_vram(old_kernel) == ("7", 921600 * 1024, "0000:03:00.0")
assert gy.fdinfo_vram(new_kernel) == ("9", 1536 * 2**20, "0000:03:00.0")
assert gy.fdinfo_vram("pos:\t0\nflags:\t02\n") is None

proc = Path(tempfile.mkdtemp())
def fake_process(pid, comm, fds):
    d = proc / str(pid)
    (d / "fd").mkdir(parents=True)
    (d / "fdinfo").mkdir()
    (d / "comm").write_text(comm + "\n")
    for fd, (target, info) in enumerate(fds):
        os.symlink(target, d / "fd" / str(fd))
        (d / "fdinfo" / str(fd)).write_text(info)
fake_process(100, "gamescope-wl", [("/dev/dri/card1", old_kernel), ("/dev/dri/renderD128", old_kernel)])
fake_process(200, "ollama", [("/dev/dri/renderD128", new_kernel)])
fake_process(300, "bash", [("/dev/pts/0", "pos:\t0\n")])
fake_process(400, "tiny", [("/dev/dri/renderD128", "drm-client-id:\t3\ndrm-memory-vram:\t1024 KiB\n")])
fake_process(500, "Xwayland", [("/dev/dri/renderD128", old_kernel)])      # gamescope's client, inherited
igpu = "drm-pdev:\t0000:0c:00.0\ndrm-client-id:\t4\ndrm-memory-vram:\t524288 KiB\n"
fake_process(600, "kwin", [("/dev/dri/card0", igpu)])                  # on the integrated GPU
(proc / "self").mkdir()
holders = gy.vram_holders(proc=str(proc), pdev="0000:03:00.0")
assert holders == [(200, "ollama", 1536 * 2**20), (100, "gamescope-wl", 921600 * 1024)], holders
assert gy.holders_text(holders) == "ollama 1.5 GB, gamescope-wl 900 MB"
assert gy.vram_holders(proc="/nonexistent") == []
ok("names the programs holding GPU memory (old and new kernels): each client once, only on the AI's card")

for msg in ["llama-server process has terminated: exit status 1: cudaMalloc failed: out of memory",
            "llama-server reported out-of-memory during startup: failed to allocate compute pp buffers",
            "vk::Device::allocateMemory: ErrorOutOfDeviceMemory", "VK_ERROR_OUT_OF_DEVICE_MEMORY",
            "model requires more system memory (24.1 GiB) than is available (19.0 GiB)",
            "ggml_backend_cuda_buffer_type_alloc_buffer: allocation failed", "failed to reserve graph",
            "insufficient memory for the KV cache"]:
    assert gy.OOM_RE.search(msg), msg
for msg in ["model 'x' not found", "invalid model name", "context canceled", "file does not exist"]:
    assert not gy.OOM_RE.search(msg), msg
ok("recognizes Ollama's out-of-memory errors (ROCm, Vulkan, system RAM), and only those")

readings = iter([9.0, 4.0, 1.2, 1.0, 1.0, 1.0, 1.0])
gy.memory_readings = lambda: {"vram_used": int(next(readings, 1.0) * 2**30)}
assert gy.wait_vram_settled(5) == 2**30
ok("waits until the GPU memory reading stops falling")


# --- the restart, and the automatic retry when a load runs out of memory
class FakeOllama:
    base = "http://stub"

    def __init__(self, fail_times):
        self.fail_times, self.calls = fail_times, []

    def start(self):
        self.calls.append("start")
        return True

    def stop(self):
        self.calls.append("stop")
        return True

    def wait_ready(self, *a):
        return True

    def request(self, path, payload=None, **k):
        return {"models": [{"name": "gpt-oss:20b", "digest": "d"}]}

    def warm(self, name=None):
        self.calls.append("warm")
        if self.fail_times:
            self.fail_times -= 1
            body = b'{"error":"llama-server process has terminated: exit status 1: cudaMalloc failed: out of memory"}'
            raise urllib.error.HTTPError(self.base, 500, "err", None, io.BytesIO(body))

    def loaded(self):
        return []


vram = {"used": 15 * 2**30}
gy.memory_readings = lambda: {"vram_used": vram["used"], "vram_total": 16 * 2**30}
def stop_and_free():
    w.ollama.calls.append("stop")
    vram["used"] = int(2.5 * 2**30)
    return True
gy.vram_holders = lambda *a, **k: [(9, "gamescope-wl", 900 * 2**20), (10, "someapp", int(1.5 * 2**30))]
gy.scan_games = lambda cfg: {}
gy.placement_note = lambda *a: (False, "")
gy.gpu_share = lambda loaded: None


def watcher(fail_times):
    global w
    w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="gpt-oss:20b", alias=""))
    w.ollama = FakeOllama(fail_times)
    w.ollama.stop = stop_and_free
    w.state = "ai"
    return w


def run_restarts(w):
    """What the main loop does: apply a requested restart."""
    why = w.take_restart()
    if why:
        w.restart_ai(why)
    return why


w = watcher(fail_times=1)
w.start_warm()
wait(lambda: w.restart_request is not None, msg="automatic restart requested")
assert "out of GPU memory" in w.warm_status
assert run_restarts(w).startswith("automatic restart")
wait(lambda: w.warm_status.startswith("ready"), msg="loaded after the restart")
texts = [e["text"] for e in w.events]
assert any("freed 12.5 GB" in t and "2.5 GB of GPU memory still in use" in t
           and "gamescope-wl 900 MB" in t for t in texts), texts
assert w.ollama.calls == ["warm", "stop", "start", "warm"], w.ollama.calls
assert w.oom_retried is False
ok("out of GPU memory: restarts Ollama once, waits for the memory, reports what's left, loads")

vram["used"] = 15 * 2**30
w = watcher(fail_times=5)
w.start_warm()
wait(lambda: w.restart_request is not None, msg="first automatic restart")
run_restarts(w)
wait(lambda: w.warm_status.startswith("failed"), msg="gives up after one restart")
assert w.restart_request is None, "must not restart in a loop"
assert "not enough GPU memory for gpt-oss:20b at context 65536" in w.warm_status
assert "someapp 1.5 GB" in w.warm_status and "context_length 32768" in w.warm_status, w.warm_status
ok("still out of memory after a restart: says who holds the memory and what to change, no loop")

w.go_off("gaming", "game started")
assert w.oom_retried is False and w.vram_report == "" and not w.restarting()
ok("a game in between gives the next out-of-memory load its own automatic restart")

# --- the page button and the AI being off
w = watcher(fail_times=0)
w.cfg["control_password"] = "hunter2222"
code, body = w.api("restart", {}, "hunter2222")
assert code == 202 and w.take_restart() == "restart requested from the status page"
w.state = "gaming"
assert w.api("restart", {}, "hunter2222")[0] == 409
w.restart_ai("restart requested")
assert "stop" not in w.ollama.calls and "nothing to restart" in w.events[0]["text"]
ok("status page: Restart the AI asks the main loop; refused (with a reason) while the AI is off")

w = watcher(fail_times=0)
w.cfg["control_password"] = "hunter2222"
w.downloads = {"big:1": {"name": "big:1", "state": "running", "use": False}}
assert w.api("restart", {}, "hunter2222")[0] == 409
w.request_restart("automatic restart after running out of GPU memory")
assert w.take_restart() is None and w.restarting(), "a restart must wait for the download"
w.downloads = {}
assert w.take_restart().startswith("automatic")
assert w.api("restart", {}, "hunter2222")[0] == 202
assert w.api("restart", {}, "hunter2222") == (409, {"error": "Already restarting."})
w.restart_signal = True
assert w.take_restart() == "restart requested from the status page" and not w.restart_signal
w.restart_active = True
w.restart_signal = True
assert w.take_restart() is None and not w.restart_signal, "a second request during a restart runs nothing"
ok("restarts wait for downloads and speed tests, and duplicates while one runs are dropped")

w = watcher(fail_times=0)
gy.scan_games = lambda cfg: {123: "Some Game (AppId 5)"}
w.restart_ai("restart requested")
assert w.ollama.calls == ["stop"], "must not start Ollama when a game started meanwhile"
gy.scan_games = lambda cfg: {}
ok("a game starting during the restart wins: Ollama stays off")

w.restart_signal = True
assert w.take_restart() == "restart requested (gpu-yield restart)" and w.take_restart() is None
ok("the signal from gpu-yield restart is picked up once")

# --- gpu-yield restart (the command): signals the watcher and follows along
got = []
signal.signal(signal.SIGUSR1, lambda *a: got.append("USR1"))
t0 = gy.time.time()
me = os.getpid()
gy.user_systemctl = lambda *a: types.SimpleNamespace(
    returncode=0, stdout=f"{me}\n" if "MainPID" in a else "", stderr="")
statuses = iter([
    {"state": "ai", "pid": me, "version": gy.VERSION, "restart_count": 2, "model_status": "ready (gpt-oss:20b)", "events": []},
    {"state": "ai", "pid": me, "restart_count": 2, "model_status": "ready (gpt-oss:20b)",
     "events": [{"at": t0 + 0.5, "text": "note: a client loaded x instead of y"}]},      # not ours yet
    {"state": "ai", "pid": me, "restart_count": 3, "restarting": True, "model_status": "restarting: freeing GPU memory",
     "events": [{"at": t0 + 1, "text": "restart requested (gpu-yield restart)"}]},
    {"state": "ai", "pid": me, "restart_count": 3, "restarting": False, "model_status": "ready (gpt-oss:20b)",
     "events": [{"at": t0 + 9, "text": "gpt-oss:20b loaded in 8.0s"},
                {"at": t0 + 3, "text": "freed 12.5 GB; 1.0 GB of GPU memory still in use"},
                {"at": t0 + 1, "text": "restart requested (gpu-yield restart)"},
                {"at": t0 - 100, "text": "older event"}]},
])
gy.read_status = lambda: next(statuses)
gy.time.sleep = lambda s: None
out = io.StringIO()
import contextlib
with contextlib.redirect_stdout(out):
    gy.cmd_restart(types.SimpleNamespace())
text = out.getvalue()
assert got == ["USR1"], got
assert "freed 12.5 GB" in text and "Done: ready" in text and "older event" not in text, text
assert text.count("restart requested") == 1
ok("gpu-yield restart signals the watcher and prints what happens until the model is loaded")

gy.read_status = lambda: {"state": "gaming", "pid": me, "version": gy.VERSION}
with contextlib.redirect_stdout(out):
    gy.cmd_restart(types.SimpleNamespace())
assert got == ["USR1"] and "Nothing to restart" in out.getvalue()
ok("gpu-yield restart during a game explains instead of signalling")

def refused(status):
    gy.read_status = lambda: status
    try:
        gy.cmd_restart(types.SimpleNamespace())
    except SystemExit as e:
        return e.code == 1
    return False
assert refused({"state": "ai", "pid": me, "version": "1.9.1"}), "an older watcher would be killed by the signal"
assert refused({"state": "ai", "pid": me + 1, "version": gy.VERSION}), "pid isn't the service's main process"
assert refused({"state": "ai", "pid": me, "version": gy.VERSION,
                "downloads": [{"name": "big:1", "state": "running"}]})
assert got == ["USR1"], "nothing may be signalled in these cases"
ok("gpu-yield restart never signals an older watcher or a process that isn't the service, or mid-download")
print("restart tests passed")
