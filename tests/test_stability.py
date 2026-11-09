"""1.13.5 stability fixes: a game always gets the GPU even if stopping Ollama fails at
first; restarts of Ollama keep a give-up; slow name lookups and ignored Steam apps don't
hold up or hide games; local apps count as use; failures in setup, firewall and config
steps are reported, not silent."""
import contextlib
import io
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _util import load, ok


def attempt(fn, *a, **k):
    try:
        fn(*a, **k)
    except SystemExit as e:
        return e.code or 0
    return 0

gy = load()
gy.os.geteuid = lambda: 1000
calls = []


def systemctl(*a):
    calls.append(a)
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


gy.user_systemctl = systemctl


class FakeOllama:
    unit = "gpu-yield-ollama.service"

    def __init__(self, stop_fails=0, raises=False):
        self.stop_fails, self.raises, self.running, self.stops = stop_fails, raises, True, 0

    def stop(self):
        self.stops += 1
        if self.raises and self.stops == 1:
            raise OSError("Resource temporarily unavailable")
        if self.stops <= self.stop_fails:
            return False
        self.running = False
        return True

    def active(self):
        return self.running

    def start(self):
        self.running = True
        return True


class FakeStandIn:
    def __init__(self):
        self.started = 0
        self.server = None
        self.retry_after = 120

    def start(self):
        self.started += 1
        self.server = object()
        return True

    def stop(self):
        self.server = None


def watcher(ollama):
    w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="m:1"))
    w.ollama, w.standin = ollama, FakeStandIn()
    w.event = lambda *a, **k: None
    w.write_status = lambda *a, **k: None
    w.state = "ai"
    return w


# --- a failed stop at game start is retried until the game gets the GPU
for o in (FakeOllama(stop_fails=2), FakeOllama(raises=True)):
    w = watcher(o)
    w.step({1: "Game"}, False, False, 100.0)
    assert w.state == "gaming" and o.running and w.standin.started == 0
    for t in range(3):
        w.step({1: "Game"}, False, False, 102.0 + 2 * t)
    assert not o.running and w.standin.started == 1 and not w.stop_pending, (o.stops, w.stop_pending)
# a stop that never works is killed outright on the third try
calls.clear()
o = FakeOllama(stop_fails=99)
o.active = lambda: not any(c[:2] == ("kill", "--signal=SIGKILL") for c in calls)
w = watcher(o)
for t in range(3):
    w.step({1: "Game"}, False, False, 100.0 + 2 * t)
assert ("kill", "--signal=SIGKILL", o.unit) in calls and not w.stop_pending
ok("a failed Ollama stop at game start is retried, then forced, until the game gets the GPU")

# --- restarting a stopped Ollama keeps a give-up and a wait before the next try
for setup in ("give_up", "backoff"):
    o = FakeOllama()
    o.running = False
    w = watcher(o)
    w.ollama.ready = lambda: True
    w.note_ollama_exit = lambda: ""
    w.last_restart = w.last_health = -1e9
    if setup == "give_up":
        w.give_up_on_ram()
    else:
        w.warm_failed_at, w.warm_retry = time.monotonic(), 300
    warms = []
    w.start_warm = lambda: warms.append(1)
    w.health(time.monotonic())
    assert o.running and not warms, setup
    assert (w.given_up() if setup == "give_up" else w.warm_failed_at is not None), setup
ok("restarting a stopped Ollama doesn't undo a give-up or the wait before the next try")

# --- the fixed name isn't copied when its record can't be saved
assert gy.save_alias_record.__doc__
real_save = gy.save_alias_record
gy.save_alias_record = lambda *a: False
copies = []


class AliasOllama:
    unit, base = "u", "http://x"

    def request(self, path, payload=None, timeout=5, method=None):
        if path == "/api/tags":
            return {"models": [{"name": "m:1", "digest": "d1", "size": 1}]}
        if path == "/api/copy":
            copies.append(payload)
        if path == "/api/ps":
            return {"models": [{"name": "m:1", "size": 1, "size_vram": 1}]}
        return {}

    def warm(self, name=None):
        self.warmed = name

    def wait_ready(self, timeout, evt=None):
        return True

    def stop(self):
        return True

    def loaded(self):
        return [{"name": "m:1", "size": 1, "size_vram": 1}]

    def ready(self):
        return True

    def start(self):
        return True

    def active(self):
        return True


w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="m:1", alias="gpu-yield"))
w.event = lambda *a, **k: None
w.ollama = AliasOllama()
w.state = "ai"
w._warm_inner(w.warm_gen)
assert not copies and "couldn't point gpu-yield" in w.alias_problem and w.loaded_name == "m:1", \
    (copies, w.alias_problem, w.loaded_name)
gy.save_alias_record = real_save
ok("the fixed name isn't copied when its record can't be saved, so it never becomes 'someone else's'")

# --- a slow game-name lookup can't hold up stopping Ollama
gy._label_cache.clear()
gy.steam_app_name = lambda appid: time.sleep(2) or "Slow Disk Game"
t = time.monotonic()
assert gy.game_label("4242", []) == "Steam AppId 4242"
assert time.monotonic() - t < 1.0
time.sleep(2.2)
assert gy.game_label("4242", []) == "Slow Disk Game (AppId 4242)", "the name arrives later"
ok("game names are looked up on the side: a sleeping or hung library disk can't delay stopping Ollama")

# --- local apps count as use; gpu-yield's own requests don't
class Slow(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        time.sleep(4)
        self.wfile.write(b"x")


srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
srv.daemon_threads = True
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
mine = threading.Thread(target=lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/").read())
mine.start()
time.sleep(0.7)
assert gy.local_connections(port) == set(), "gpu-yield's own connection doesn't count"
mine.join()
app = subprocess.Popen([sys.executable, "-c",
                        f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/').read()"])
time.sleep(1.0)
assert len(gy.local_connections(port)) == 1, "another program on this PC does"
w = watcher(FakeOllama())
w.cfg["port"] = port
w.track_use()
assert not w.in_use, "one poll isn't enough"
w.track_use()
assert w.in_use and w.last_used, "seen on two polls: in use"
app.wait()
srv.shutdown()
ok("an app or download on this PC that stays connected counts as use (auto-updates wait for it)")

# --- the firewall step says when it failed
gy2 = load()
gy2.run = lambda cmd, *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="")
gy2.lan_address = lambda: ("192.168.1.5", "192.168.1.0/24")
gy2.sudo = lambda cmd: "--reload" not in cmd and "fe80" not in " ".join(cmd)
err = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
    assert gy2.open_firewall(dict(gy2.DEFAULTS), types.SimpleNamespace(yes=True)) is False
assert "gpu-yield firewall" in err.getvalue(), err.getvalue()
ok("a firewall step that fails is reported, with how to retry")

# --- config: OLLAMA_HOST is gpu-yield's; changes that restart wait for downloads
gy.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
if not gy.CONFIG_FILE.exists():
    gy.write_private(gy.CONFIG_FILE, gy.CONFIG_TEMPLATE)
with contextlib.redirect_stderr(io.StringIO()) as err:
    assert attempt(gy.cmd_config, types.SimpleNamespace(key="env.OLLAMA_HOST", value="0.0.0.0:9999")) != 0
assert "set by gpu-yield" in err.getvalue()
gy.read_status = lambda: {"downloads": [{"name": "qwen3:14b", "state": "running"}]}
before = gy.CONFIG_FILE.read_text()
with contextlib.redirect_stderr(io.StringIO()) as err:
    assert attempt(gy.cmd_config, types.SimpleNamespace(key="cooldown_seconds", value="60")) != 0
assert "download (qwen3:14b)" in err.getvalue() and gy.CONFIG_FILE.read_text() == before
ok("config refuses changing OLLAMA_HOST, and waits for a running download before restarting")

# --- after an update, a watcher that's running but not enabled is restarted too
calls.clear()
gy.user_systemctl = lambda *a: calls.append(a) or types.SimpleNamespace(
    returncode=1 if a[0] == "is-enabled" else 0, stdout="", stderr="")
gy.load_config = lambda: dict(gy.DEFAULTS)
gy.write_unit = lambda *a: None
gy.ensure_ollama_unit = lambda cfg: None
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    gy.cmd_post_update(types.SimpleNamespace())
assert ("try-restart", gy.WATCHER_UNIT) in calls, calls
ok("after an update, a watcher started by hand is restarted on the new version too")

print("stability tests passed")
