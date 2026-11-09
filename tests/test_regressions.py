"""Bugs found in the 1.9.0 reviews, one check each."""
import http.client
import json
import os
import socket
import threading
import time
import types
import urllib.error

from _util import free_port, load, ok, wait

gy = load()
gy.os.geteuid = lambda: 1000            # the sandbox runs as root; these commands refuse root
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


# --- config: whole numbers, raw values checked, file restored, private, multi-line lists
assert gy.parse_setting("port", "11434.0") == 11434 and type(gy.parse_setting("port", "11434.0")) is int
assert gy.parse_setting("poll_seconds", "1.5") == 1.5 and gy.parse_setting("cooldown_seconds", "90") == 90
try:
    gy.parse_setting("context_length", "65536.5")
    raise AssertionError("accepted a fractional context length")
except ValueError:
    pass
before = gy.CONFIG_FILE.read_text()
assert attempt(gy.change_settings, {"poll_seconds": 0.01}) and gy.CONFIG_FILE.read_text() == before
assert attempt(gy.change_settings, {"context_length": 64}) and gy.CONFIG_FILE.read_text() == before
assert gy.config_problem(dict(gy.DEFAULTS, port=11434.5)).startswith("port must be a whole number")
gy.CONFIG_FILE.write_text(before.replace("port = 11434", "port = 11434.0"))
assert type(gy.load_config()["port"]) is int
ok("whole-number settings stay whole; out-of-range values are refused, not clamped into the file")

os.chmod(gy.CONFIG_FILE, 0o644)
gy.change_settings({"cooldown_seconds": 60})
assert os.stat(gy.CONFIG_FILE).st_mode & 0o777 == 0o600
ok("the config file is private (0600) after any change: it can hold passwords")

text = gy.CONFIG_FILE.read_text().replace("extra_processes = []",
                                          'extra_processes = [\n  "factorio",  # a comment ]\n  "x]y",\n]')
gy.CONFIG_FILE.write_text(text)
assert gy.load_config()["extra_processes"] == ["factorio", "x]y"]
gy.change_settings({"extra_processes": ["a", "b"]})
cfg = gy.load_config()
assert cfg["extra_processes"] == ["a", "b"] and cfg["ignore_appids"] == []
ok("a list written over several lines is replaced whole")

# --- model command refuses the fixed name before downloading anything
pulled = []
gy.pull_and_load = lambda cfg, name: pulled.append(name) or True
gy.Ollama.ready = lambda self: True
assert attempt(gy.cmd_model, types.SimpleNamespace(name="gpu-yield")) and not pulled
assert gy.load_config()["model"] == ""
ok("gpu-yield model refuses the fixed name before downloading")

# --- setup: a failed Ollama reinstall leaves the watcher running (the download happens
# before anything is stopped)
calls.clear()
gy.check_conflicts = lambda args: None
def broken_install(backend):
    gy.die("download failed")
gy.stage_ollama = broken_install
gy.OLLAMA_BIN.parent.mkdir(parents=True, exist_ok=True)
gy.OLLAMA_BIN.write_text("")
args = types.SimpleNamespace(model="m:1", context=None, backend=None, reinstall=True, yes=True,
                             skip_pull=True, no_firewall=True)
assert attempt(gy.cmd_setup, args)
assert ("stop", gy.WATCHER_UNIT) not in calls, calls
# and if a later step fails once the watcher is stopped, it's started again
gy.stage_ollama = lambda backend: gy.HOME / "staged"
gy.swap_ollama = lambda staged: gy.die("disk full")
calls.clear()
assert attempt(gy.cmd_setup, args)
assert ("stop", gy.WATCHER_UNIT) in calls and calls[-1] == ("start", gy.WATCHER_UNIT), calls
args.context, args.model = 64, None
before = gy.CONFIG_FILE.read_text()
assert attempt(gy.cmd_setup, args) and gy.CONFIG_FILE.read_text() == before
ok("setup: a failed reinstall restarts the watcher; a bad --context leaves the file alone")

# --- update: a connection cut off mid-download is a clean error
def cut_off(*a, **k):
    raise http.client.IncompleteRead(b"partial")
gy.github_get = cut_off
assert attempt(gy.fetch_latest, gy.load_config()) == 1
ok("update: a dropped connection gives a message, not a traceback")

# --- auto-update waits for downloads, speed tests and loads; counts days while asleep
w = gy.Watcher(dict(gy.DEFAULTS, auto_update="gpu-yield", port=1))
w.state, w.in_use, w.last_used = "ai", False, None
w.next_update_check = 0
w.downloads = {"big": {"name": "big", "state": "running", "use": False}}
started = []
w._auto_update = lambda mode: started.append(mode)
w.maybe_auto_update()
assert not started and w.next_update_check > time.clock_gettime(time.CLOCK_BOOTTIME) + 60
w.downloads, w.next_update_check = {}, 0
w.maybe_auto_update()
assert started == ["gpu-yield"]
ok("auto-update waits for a running download; scheduled on the clock that counts sleep")

# --- status page: host names a website can't control
me = socket.gethostname().lower().split(".")[0]
allowed = gy.StatusServer.host_allowed
assert allowed(f"{me}:11435") and allowed(f"{me}.local") and allowed(f"{me}.lan.") and allowed("192.168.1.5:11435")
assert not allowed(f"{me}.attacker.example") and not allowed(f"{me}.local.attacker.example")
gy._read_text, real_read = (lambda p: "search fritz.box\n" if str(p) == "/etc/resolv.conf" else real_read(p)), gy._read_text
assert allowed(f"{me}.fritz.box:11435")
gy._read_text = real_read
hint = gy.StatusServer(11435, None).host_hint()
assert f"{me}.local:11435" in hint
ok("status page: only this PC's own names count (no <name>.attacker.example), with a hint")

# --- status page: slow clients are cut off, and one address can't take every slot
class W:
    def snapshot(self): return {"state": "ai"}
gy.PageServer.DEADLINE = 1.5
port = free_port()
page = gy.StatusServer(port, W())
assert page.start()
slow = [socket.create_connection(("127.0.0.1", port)) for _ in range(gy.PageServer.PER_CLIENT)]
for c in slow:
    c.sendall(b"GET /status HTTP/1.1\r\n")
time.sleep(0.3)
extra = socket.create_connection(("127.0.0.1", port))
extra.settimeout(3)
extra.sendall(b"GET /status HTTP/1.0\r\n\r\n")
assert extra.recv(100) == b"", "a 7th connection from one address should be refused"
for c in slow:
    c.settimeout(4)
    assert c.recv(100) == b"", "a trickling connection should be cut at the deadline"
for c in slow:
    c.close()
wait(lambda: not page.server.per_ip, msg="slots released")
c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
c.request("GET", "/status")
assert c.getresponse().status == 200
page.stop()
ok("status page: a per-client cap and a deadline per request; it answers again afterwards")

assert gy.split_host(gy.host_key("fd00::5", 8080)) == ("fd00::5", 8080)
assert gy.host_key("fd00::5", 11435) == "fd00::5"
ok("remembered IPv6 hosts read back correctly")

# --- deleting a model that is about to be used
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="a:1"))
w.state = "ai"
w.ollama.ready = lambda: True
w.actions.put(("model", "b:1"))
code, body = w._api_delete({"name": "b:1"})
assert code == 409 and "about to become" in body["error"]
w.actions.queue.clear()
w.downloads = {"c:1": {"name": "c:1", "state": "running", "use": True}}
assert w._api_delete({"name": "c:1"})[0] == 409
w.downloads = {}
w.api_models = lambda: {"models": [{"name": "b:1"}], "available": True}
w.deleting.add("b:1")
assert w._api_model({"name": "b:1"})[0] == 409, "switched to a model being deleted"
w.deleting.clear()
assert w._api_model({"name": "b:1"})[0] == 202 and w._api_delete({"name": "b:1"})[0] == 409
ok("can't delete a model that's queued to be used, or downloading to be used, or vice versa")

# --- wake keeps waiting while the PC boots (refused before the service starts)
gy.load_hosts = lambda: {"pc": {"mac": "1c:2b:3a:4d:5e:6f"}}
gy.send_magic_packet = lambda *a: None
probes = iter([urllib.error.URLError(TimeoutError()), urllib.error.URLError(ConnectionRefusedError()),
               urllib.error.URLError(ConnectionRefusedError()), {"state": "ai", "model_status": "loading"},
               {"state": "ai", "ai_available": True, "model_status": "ready (m)"}])
def probe(*a, **k):
    p = next(probes)
    if isinstance(p, Exception):
        raise p
    return p
gy.fetch_remote_status = probe
real_sleep = gy.time.sleep
gy.time.sleep = lambda s: None
gy.cmd_wake(types.SimpleNamespace(target="pc", no_wait=False))
gy.time.sleep = real_sleep
assert next(probes, "used") == "used", "wake gave up while the PC was still starting"
ok("wake keeps waiting through a boot instead of stopping at the first refused connection")

# --- the fixed name survives an interruption between the copy and saving the record
gy.ALIAS_FILE.unlink(missing_ok=True)
gy.save_alias_record("gpu-yield", "old")
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="b:1"))
class Flaky:
    base = "http://stub"
    def __init__(self): self.tags = [{"name": "b:1", "digest": "new"}, {"name": "gpu-yield:latest", "digest": "old"}]
    def wait_ready(self, *a): return True
    def request(self, path, payload=None, **k):
        if path == "/api/copy":
            raise OSError("Ollama stopped (a game started)")
        return {"models": self.tags}
    def warm(self, name=None): pass
    def loaded(self): return []
w.ollama = Flaky()
w._warm_inner(w.warm_gen)
rec = gy.alias_record()
assert gy.alias_owns(rec, "old") and gy.alias_owns(rec, "new"), rec
w.ollama.tags[1]["digest"] = "new"          # suppose the copy did happen before the error
assert gy.alias_owns(gy.alias_record(), "new")
ok("the fixed name stays gpu-yield's own if a copy is interrupted")

# --- a slow-starting Ollama isn't restarted while it's still starting
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="m"))
w.state, w.last_restart = "ai", 1000.0
w.ollama.active = lambda: True
w.ollama.ready = lambda: False
stops = []
w.ollama.stop = lambda: stops.append(1)
w.go_on = lambda why, load=True: stops.append(why)
for t in range(15, 120, 15):
    w.last_health = 0
    w.health(1000.0 + t)
assert not stops and w.ping_failures == 0
for t in (120, 135, 150):
    w.last_health = 0
    w.health(1000.0 + t)
assert stops, "a hung Ollama should still be restarted once it's had time to start"
ok("a slow Ollama start gets 2 minutes before silence counts against it")

# --- Windows games during cooldown count at once (Ollama is already off)
w = gy.Watcher(dict(gy.DEFAULTS, port=1))
w.state = "cooldown"
wine = {123: "game.exe" + gy.WINE_SUFFIX}
assert w.confirm_wine(dict(wine), 0.0) == wine
w.state = "ai"
assert w.confirm_wine(dict(wine), 0.0) == {}
ok("a Windows game starting during cooldown counts at once")
print("regression tests passed")
