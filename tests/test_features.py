"""1.6 features: use tracking, remote status and wake, backend choice, config command,
changelog, automatic updates."""
import json
import threading
import time
import types
from pathlib import Path

from _util import free_port, load, ok

gy = load()
calls = []
gy.user_systemctl = lambda *a: calls.append(a) or types.SimpleNamespace(returncode=0, stdout="", stderr="")

# --- who is using the AI: /proc/net/tcp parsing
tcp = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000:2CAA 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 1
   1: 0100007F:2CAA 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1000        0 2
   2: 1700A8C0:2CAA 1600A8C0:C350 01 00000000:00000000 00:00000000 00000000  1000        0 3
   3: 1700A8C0:2CAA 1600A8C0:C351 06 00000000:00000000 00:00000000 00000000  1000        0 4
   4: 1700A8C0:0016 1600A8C0:C352 01 00000000:00000000 00:00000000 00000000  1000        0 5
"""
tcp6 = """  sl  local_address                         remote_address                        st
   0: 00000000000000000000000000000000:2CAA 00000000000000000000000001000000:D432 01
   1: 0000000000000000FFFF00001700A8C0:2CAA 0000000000000000FFFF00001600A8C0:C353 01
"""
real_read = gy._read_text
gy._read_text = lambda p: {"/proc/net/tcp": tcp, "/proc/net/tcp6": tcp6}.get(str(p)) or real_read(p)
assert gy.client_connections(11434) == 3, gy.client_connections(11434)   # 2 v4 (one closing) + 1 mapped v6
assert gy.client_connections(22) == 1
gy._read_text = real_read
ok("counts other machines' connections to Ollama, ignoring loopback and listeners")

# --- backend choice from the GPU architecture
kfd = Path(gy.HOME) / "kfd"
def gpus(*versions):
    for d in kfd.glob("*"):
        for f in d.iterdir(): f.unlink()
        d.rmdir()
    for i, v in enumerate(versions):
        (kfd / str(i)).mkdir(parents=True)
        (kfd / str(i) / "properties").write_text(f"cpu_cores_count 0\ngfx_target_version {v}\n")
gy.KFD_NODES = kfd
auto = dict(gy.DEFAULTS)
gpus(0, 110001)                     # CPU node + RX 7800 XT
assert gy.gpu_targets() == {"gfx1101"} and gy.resolve_backend(auto) == "rocm"
gpus(0, 100301)                     # RX 6700 XT
assert gy.gpu_targets() == {"gfx1031"} and gy.resolve_backend(auto) == "vulkan"
gpus(90010)
assert gy.gpu_targets() == {"gfx90a"}
gpus()
assert gy.resolve_backend(auto) == "vulkan"
assert gy.resolve_backend(dict(auto, backend="rocm")) == "rocm"
assert "OLLAMA_VULKAN" not in gy.ollama_unit_text(dict(auto, backend="rocm"))
assert 'OLLAMA_VULKAN=1' in gy.ollama_unit_text(dict(auto, backend="vulkan"))
ok("auto backend: ROCm for a 7800 XT, Vulkan for a 6700 XT or no AMD GPU")

# --- config validation and the config command
assert gy.config_problem(dict(gy.DEFAULTS)) is None
for bad, why in ((dict(backend="metal"), "backend"), (dict(port="11434"), "port"),
                 (dict(status_port=11434), "differ"), (dict(detect_wine="yes"), "detect_wine"),
                 (dict(poll_seconds=0.1), "poll_seconds"), (dict(auto_update="weekly"), "auto_update")):
    assert why in (gy.config_problem(dict(gy.DEFAULTS, **bad)) or ""), bad
ok("config validation rejects wrong types, unknown choices and out-of-range values")

gy.CONFIG_DIR.mkdir(parents=True)
gy.CONFIG_FILE.write_text(gy.CONFIG_TEMPLATE)
def config(key=None, value=None):
    try:
        gy.cmd_config(types.SimpleNamespace(key=key, value=value))
        return 0
    except SystemExit as e:
        return e.code
assert config("cooldown_seconds", "90") == 0 and gy.load_config()["cooldown_seconds"] == 90
assert config("detect_wine", "off") == 0 and gy.load_config()["detect_wine"] is False
assert config("ignore_appids", "123, 456") == 0 and gy.load_config()["ignore_appids"] == ["123", "456"]
assert config("env.OLLAMA_KV_CACHE_TYPE", "q8_0") == 0
assert gy.load_config()["ollama_env"] == {"OLLAMA_KV_CACHE_TYPE": "q8_0"}
assert config("env.OLLAMA_KV_CACHE_TYPE", "") == 0 and gy.load_config()["ollama_env"] == {}
before = gy.CONFIG_FILE.read_text()
assert config("backend", "metal") != 0 and gy.CONFIG_FILE.read_text() == before
assert config("cooldown_seconds", "soon") != 0 and gy.CONFIG_FILE.read_text() == before
assert config("nonsense", "1") != 0 and config("model", "x") != 0
assert "# Model kept loaded" in gy.CONFIG_FILE.read_text()
ok("config command sets values by type, edits Ollama variables, refuses bad values unchanged")

# --- changelog
log = """# Changelog\n\n## 1.7.0 (x)\n- seven\n\n## 1.6.1 (x)\n- six one\n\n## 1.6.0 (x)\n- six\n"""
got = gy.changelog_entries(log, "1.6.0", "1.7.0")
assert [g.split()[1] for g in got] == ["1.7.0", "1.6.1"], got
assert gy.changelog_entries(log, "1.7.0", "1.7.0") == []
real = (Path(gy.__file__).parent / "CHANGELOG.md").read_text()
assert gy.changelog_entries(real, "0", gy.VERSION)[0].startswith(f"## {gy.VERSION} "), \
    "CHANGELOG.md has no entry for the current VERSION"
ok("changelog: picks the entries between two versions; CHANGELOG.md covers this version")

# --- remote status and wake, against a real status page
class FakeWatcher:
    def snapshot(self):
        return {"host": "livingroom", "state": "ai", "ai_available": True, "since": time.time(),
                "model": "gpt-oss:20b", "context_length": 65536, "model_status": "ready",
                "mac": "1c:2b:3a:4d:5e:6f", "version": gy.VERSION, "games": []}
port = free_port()
page = gy.StatusServer(port, FakeWatcher())
assert page.start()
target = f"127.0.0.1:{port}"
gy.cmd_status(types.SimpleNamespace(host=target))
assert gy.load_hosts()[target]["mac"] == "1c:2b:3a:4d:5e:6f"
ok("status --host reads another PC's status and remembers its MAC for waking")

sent = []
gy.send_magic_packet = lambda mac, ip=None: sent.append((mac, ip))
gy.cmd_wake(types.SimpleNamespace(target=target, no_wait=True))
assert sent == [], "should not wake a PC that's already awake"
page.stop()
gy.cmd_wake(types.SimpleNamespace(target=target, no_wait=True))
assert sent == [], "a refused connection means the PC is awake with its page off"
real_fetch = gy.fetch_remote_status
def asleep(*a, **k):                     # a sleeping PC doesn't answer at all
    raise gy.urllib.error.URLError(TimeoutError("timed out"))
gy.fetch_remote_status = asleep
gy.cmd_wake(types.SimpleNamespace(target=target, no_wait=True))
gy.fetch_remote_status = real_fetch
assert sent == [("1c:2b:3a:4d:5e:6f", "127.0.0.1")]
gy.cmd_wake(types.SimpleNamespace(target="AA:BB:CC:DD:EE:FF", no_wait=True))
assert sent[-1][0] == "AA:BB:CC:DD:EE:FF"
pkt = gy.magic_packet("aa:bb:cc:dd:ee:ff")
assert pkt[:6] == b"\xff" * 6 and pkt[6:] == bytes.fromhex("aabbccddeeff") * 16 and len(pkt) == 102
try:
    gy.cmd_wake(types.SimpleNamespace(target="unknown-host.invalid", no_wait=True))
    raise AssertionError("expected a helpful error")
except SystemExit:
    pass
ok("wake: skips an awake PC (even with its page off), uses the remembered MAC when asleep, accepts a MAC directly")

# --- automatic updates: only when idle, never during games, through systemd-run
started = []
gy.run = lambda cmd, **kw: started.append(cmd) or types.SimpleNamespace(returncode=0, stdout="", stderr="")
newer = gy.VERSION.rsplit(".", 1)[0] + "." + str(int(gy.VERSION.rsplit(".", 1)[1]) + 1)
gy.fetch_latest = lambda cfg: ("me/gy", f'VERSION = "{newer}"\n'.encode())
w = gy.Watcher(dict(gy.DEFAULTS, auto_update="gpu-yield", port=1))
w.state, now = "ai", time.clock_gettime(time.CLOCK_BOOTTIME)

def attempt(**state):
    for k, v in state.items():
        setattr(w, k, v)
    w.next_update_check = 0
    started.clear()
    w.maybe_auto_update(now)
    deadline = time.time() + 3
    while w.updating and time.time() < deadline:
        time.sleep(0.02)
    return started[:]

assert attempt(state="gaming") == []
assert attempt(state="ai", in_use=True) == []
assert attempt(in_use=False, last_used=time.time() - 60) == []
cmd = attempt(last_used=time.time() - 3600)
assert cmd and cmd[0][:2] == ["systemd-run", "--user"] and cmd[0][-2:] == ["update", "--if-idle"], cmd
assert w.next_update_check > now + 23 * 3600
w.cfg = dict(w.cfg, auto_update="off")
assert attempt(last_used=None) == []
gy.fetch_latest = lambda cfg: ("me/gy", f'VERSION = "{gy.VERSION}"\n'.encode())
w.cfg = dict(w.cfg, auto_update="gpu-yield")
assert attempt() == []
ok("auto-update: waits for no game and 15 idle minutes, runs outside the watcher, daily")

# a game starting while the update check runs: no update
real_scan = gy.scan_games
gy.scan_games = lambda cfg: {1: "Some Game (AppId 1)"}
assert attempt(state="ai", in_use=False, last_used=None) == []
gy.scan_games = real_scan
ok("auto-update: re-checks for a game right before installing")

# a rollback holds automatic updates back from the version rolled away from
gy.STATE_DIR.mkdir(parents=True, exist_ok=True)
gy.fetch_latest = lambda cfg: ("me/gy", f'VERSION = "{newer}"\n'.encode())
assert attempt(last_used=None), "precondition: an update would normally run here"
gy.note_skip(newer, gy.VERSION)                 # rolled back from `newer` to this version
assert gy.auto_update_blocked(newer) and not gy.auto_update_blocked(newer + ".1")
assert attempt(last_used=None) == [], "auto-update reinstalled the version the user rolled back from"
gy.fetch_latest = lambda cfg: ("me/gy", f'VERSION = "{newer}.1"\n'.encode())
assert attempt(last_used=None), "a release newer than the rolled-back one should still install"
gy.fetch_latest = lambda cfg: ("me/gy", f'VERSION = "{newer}"\n'.encode())
gy.note_skip(gy.VERSION, newer)                 # redo: going forward clears the hold
assert not gy.SKIP_FILE.exists() and attempt(last_used=None)
ok("auto-update: skips a version you rolled back from until you update by hand")

# configs written for older versions keep working (with a warning) instead of keeping the AI off
gy.CONFIG_FILE.write_text('poll_seconds = 0.25\nport = 11435\nignore_appids = "123"\n'
                          'context_length = "32768"\ndetect_wine = "no"\n')
cfg = gy.load_config()
assert (cfg["poll_seconds"], cfg["status_port"], cfg["ignore_appids"], cfg["context_length"],
        cfg["detect_wine"]) == (0.5, 11436, ["123"], 32768, False), cfg
ok("older configs: out-of-range, string-typed and port-clashing values are repaired, not fatal")

# TOML edits survive unusual but valid files
gy.CONFIG_FILE.write_text('model = "x"\n\n[ollama_env]   # extra variables\nA = "1"\n')
gy.set_env_value("B", "2")
assert gy.load_config()["ollama_env"] == {"A": "1", "B": "2"}
gy.CONFIG_FILE.write_text('ollama_env = { A = "1" }\n')
try:
    gy.set_env_value("B", "2"); raise AssertionError("inline table should be refused")
except SystemExit:
    pass
gy.CONFIG_FILE.write_text("")
gy.set_config_values({"extra_processes": ['it\'s "x"', "a\\b"]})
assert gy.load_config()["extra_processes"] == ['it\'s "x"', "a\\b"]
ok("config edits: commented table headers, inline tables refused, lists with quotes/backslashes")

# host names as people type or paste them
for given, want in (("pc", ("pc", 11435)), ("pc:9000", ("pc", 9000)),
                    ("http://pc:11435/", ("pc", 11435)), ("[fd00::5]:11435", ("fd00::5", 11435)),
                    ("fd00::5", ("fd00::5", 11435))):
    assert gy.split_host(given) == want, (given, gy.split_host(given))
for bad in ("pc:abc", "pc:0", "http://", ":11435"):
    try:
        gy.split_host(bad); raise AssertionError(bad)
    except ValueError:
        pass
ok("host names: pasted URLs, IPv6 and bad ports are handled")

# status reports "starting", not a stale "ready", while Ollama restarts; MAC learned late
w2 = gy.Watcher(dict(gy.DEFAULTS, port=1, model="m"))
w2.warm_status = "ready (m)"
w2.ollama.start = lambda: False
w2.go_on("restarting")
assert not w2.snapshot()["ai_available"]
w2.mac = None
gy.nic_info = lambda: ("enp5s0", "aa:bb:cc:dd:ee:ff", False)
assert w2.snapshot()["mac"] == "aa:bb:cc:dd:ee:ff"
ok("status page: no stale 'ready' during a restart; MAC picked up once the network is up")

# the remote commands work on a computer without os.getuid (Windows)
import subprocess, sys
probe = ("import os, runpy, sys; del os.getuid; del os.geteuid; "
         "sys.argv = ['gpu-yield', 'status', '--host', '127.0.0.1:1']; "
         f"runpy.run_path({str(gy.__file__)!r}, run_name='__main__')")
r = subprocess.run([sys.executable, "-B", "-c", probe], capture_output=True, text=True, timeout=30)
assert r.returncode == 1 and "nothing answers on port 1" in r.stderr and "status_page true" in r.stderr and "Traceback" not in r.stderr, r.stderr
ok("status --host runs on a system without os.getuid (Windows)")

# ---------------------------------------------------------------- 1.8 review fixes
import urllib.error


class StubOllama:
    """Just enough of Ollama's API for the fixed-name logic."""
    def __init__(self, tags, copy_error=None):
        self.tags, self.copy_error, self.calls, self.base = tags, copy_error, [], "http://stub"
        self.cfg = None

    def request(self, path, payload=None, timeout=5.0, method=None):
        self.calls.append((path, payload))
        if path == "/api/tags":
            return {"models": list(self.tags)}
        if path == "/api/copy":
            if self.copy_error:
                raise urllib.error.HTTPError(self.base, self.copy_error, "boom", None, None)
            src = next(m for m in self.tags if m["name"] == payload["source"])
            self.tags = [m for m in self.tags if not gy.same_model(m["name"], payload["destination"])]
            self.tags.append({"name": payload["destination"] + ":latest", "digest": src["digest"]})
            return {}
        if path == "/api/delete":
            self.tags = [m for m in self.tags if not gy.same_model(m["name"], payload["model"])]
            return {}
        return {}

    def wait_ready(self, *a): return True
    def warm(self, name=None): self.calls.append(("warm", name))
    def loaded(self): return []
    def is_moe(self, m): return False


def warm_with(tags, alias="gpu-yield", copy_error=None):
    wx = gy.Watcher(dict(gy.DEFAULTS, port=1, model="gpt-oss:20b", alias=alias))
    wx.ollama = StubOllama(tags, copy_error)
    wx.ollama.cfg = wx.cfg
    wx._warm_inner(wx.warm_gen)
    return wx


gy.ALIAS_FILE.unlink(missing_ok=True)
base = [{"name": "gpt-oss:20b", "digest": "d1"}]
wx = warm_with(base)
rec = gy.alias_record()
assert ("warm", "gpu-yield") in wx.ollama.calls and (rec["name"], rec["digest"], rec["digests"]) == ("gpu-yield", "d1", ["d1"])
wx = warm_with(wx.ollama.tags)                    # ours already: re-pointed freely
assert wx.loaded_name == "gpu-yield" and not wx.alias_problem
gy.ALIAS_FILE.unlink()
wx = warm_with(base + [{"name": "gpu-yield:latest", "digest": "someone-elses"}])
assert not any(c[0] == "/api/copy" for c in wx.ollama.calls), "overwrote a model gpu-yield didn't make"
assert wx.loaded_name == "gpt-oss:20b" and "won't overwrite" in wx.alias_problem
assert any(e["kind"] == "error" and "won't overwrite" in e["text"] for e in wx.events)
assert wx.snapshot()["alias"] == ""
ok("the fixed name never overwrites a model gpu-yield didn't create")

gy.save_alias_record("old-name", "d1")
wx = warm_with(base + [{"name": "old-name:latest", "digest": "d1"}], alias="new-name")
assert ("/api/delete", {"model": "old-name"}) in wx.ollama.calls and gy.alias_record()["name"] == "new-name"
wx = warm_with(wx.ollama.tags, alias="")
assert ("/api/delete", {"model": "new-name"}) in wx.ollama.calls and gy.alias_record() == {}
assert ("warm", "gpt-oss:20b") in wx.ollama.calls
ok("renaming or turning off the fixed name removes the old copy gpu-yield made")

gy.ALIAS_FILE.unlink(missing_ok=True)
wx = warm_with(base, copy_error=500)
assert wx.loaded_name == "gpt-oss:20b" and "couldn't point" in wx.alias_problem
wx.warm_status = "ready"
wx.check_loaded([{"name": "gpu-yield:latest"}])
assert "instead of" in wx.notice, "a stale fixed name must not count as the loaded model"
assert wx.snapshot()["alias"] == ""
ok("if the fixed name can't be updated, it isn't trusted or advertised, and status says so")

gy.CONFIG_FILE.write_text('model = "gpu-yield"\n')
assert gy.load_config()["alias"] == ""
gy.CONFIG_FILE.write_text('model = "gpu-yield"\nalias = "gpu-yield"\n')
try:
    gy.load_config(); raise AssertionError("an explicit clash should be reported")
except SystemExit:
    pass
gy.CONFIG_FILE.write_text("")
ok("upgrading with a model already named gpu-yield turns the fixed name off instead of the AI")

# events: folded repeats, explicit kinds, damaged status file
wx = gy.Watcher(dict(gy.DEFAULTS, port=1))
for _ in range(5):
    wx.event("failed: x is not downloaded")
assert len(wx.events) == 1 and wx.events[0]["count"] == 5
wx.event("the model is running entirely on the CPU", "error")
assert wx.events[0]["kind"] == "error"
gy.write_status_file({"events": None, "last_used": None})
ws = gy.Watcher(dict(gy.DEFAULTS, port=free_port(), status_port=0))
ws.ollama = StubOllama(base); ws.ollama.active = lambda: True; ws.ollama.start = lambda: True
ws.ollama.stop = lambda: True; ws.ollama.ready = lambda: True
real_unit = gy.ensure_ollama_unit; gy.ensure_ollama_unit = lambda c: False
th = threading.Thread(target=ws.run, daemon=True); th.start()
time.sleep(1.0); ws.stop_evt.set(); th.join(5)
gy.ensure_ollama_unit = real_unit
assert not th.is_alive() and ws.state == "ai", "a damaged status file must not stop the watcher"
ok("events: repeats fold into one with a count; a damaged status file is ignored")

# speed tests: results from the command line show up; no surprise tests while in use
wx = gy.Watcher(dict(gy.DEFAULTS, port=1, control_password="hunter22", model="m"))
before = len(wx.current_speed_results())
time.sleep(0.02)
gy.save_speed_result({"model": "m", "context_length": 1, "at": time.time(), "prompt_tokens": 1,
                      "read_tps": 1.0, "gen_tokens": 1, "write_tps": 1.0})
assert len(wx.current_speed_results()) == before + 1
wx.state, wx.warm_status, wx.in_use = "ai", "ready", True
code, body = wx.api("speed", {}, "hunter22")
assert code == 409 and body.get("busy")
wx.ollama.request = lambda *a, **k: {"prompt_eval_count": 1, "prompt_eval_duration": 1, "eval_count": 1, "eval_duration": 1}
assert wx.api("speed", {"force": True}, "hunter22")[0] == 202
ok("speed test: command-line results appear on the page; asks first while the AI is in use")
# status page switch
assert gy.config_problem(dict(gy.DEFAULTS, status_page="no")).startswith("status_page must be true or false")
assert gy.page_port(dict(gy.DEFAULTS)) == 11435 and gy.page_port(dict(gy.DEFAULTS, status_page=False)) == 0
assert gy.page_port(dict(gy.DEFAULTS, status_port=0)) == 0          # the old way still works
assert gy.config_problem(dict(gy.DEFAULTS, status_page=False, status_port=11434)) is None
assert "status_page = true" in gy.CONFIG_TEMPLATE and "status_page" in gy.LIVE_SETTINGS
ok("status_page setting: validated, and status_port = 0 still turns the page off")
print("feature tests passed")
