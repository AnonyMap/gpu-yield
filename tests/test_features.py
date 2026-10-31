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
page = gy.StatusServer(port, FakeWatcher().snapshot)
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
ok("wake: skips an awake PC, uses the remembered MAC when asleep, accepts a MAC directly")

# --- automatic updates: only when idle, never during games, through systemd-run
started = []
gy.run = lambda cmd, **kw: started.append(cmd) or types.SimpleNamespace(returncode=0, stdout="", stderr="")
newer = gy.VERSION.rsplit(".", 1)[0] + "." + str(int(gy.VERSION.rsplit(".", 1)[1]) + 1)
gy.fetch_latest = lambda cfg: ("me/gy", f'VERSION = "{newer}"\n'.encode())
w = gy.Watcher(dict(gy.DEFAULTS, auto_update="gpu-yield", port=1))
w.state, now = "ai", time.monotonic()

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
assert cmd and cmd[0][:2] == ["systemd-run", "--user"] and cmd[0][-1] == "update", cmd
assert w.next_update_check > now + 23 * 3600
w.cfg = dict(w.cfg, auto_update="off")
assert attempt(last_used=None) == []
gy.fetch_latest = lambda cfg: ("me/gy", f'VERSION = "{gy.VERSION}"\n'.encode())
w.cfg = dict(w.cfg, auto_update="gpu-yield")
assert attempt() == []
ok("auto-update: waits for no game and 15 idle minutes, runs outside the watcher, daily")
print("feature tests passed")
