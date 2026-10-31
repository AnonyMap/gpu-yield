"""Config editing, generated unit files, and the safe behaviour on a broken config."""
import http.client
import json
import shutil
import subprocess
import threading
import time
import types

from _util import free_port, load, ok, wait

gy = load()
calls = []
gy.user_systemctl = lambda *a: calls.append(a) or types.SimpleNamespace(returncode=0, stdout="", stderr="")
gy.CONFIG_DIR.mkdir(parents=True)

# --- set_config_values
gy.CONFIG_FILE.write_text(gy.CONFIG_TEMPLATE)
gy.set_config_values({"model": "qwen3.5:9b", "context_length": 32768, "detect_wine": False})
cfg = gy.load_config()
assert (cfg["model"], cfg["context_length"], cfg["detect_wine"]) == ("qwen3.5:9b", 32768, False)
assert "# Model kept loaded" in gy.CONFIG_FILE.read_text()
ok("edits the template in place and keeps its comments")

gy.CONFIG_FILE.write_text('"model" = "a"\n\n[ollama_env]\nmodel = "not-top-level"\n')
gy.set_config_values({"model": "b", "port": 11500})
cfg = gy.load_config()
assert cfg["model"] == "b" and cfg["port"] == 11500 and cfg["ollama_env"] == {"model": "not-top-level"}
assert not gy.CONFIG_FILE.with_suffix(".tmp").exists()
ok("handles quoted keys, never touches keys inside [ollama_env], writes atomically")

# --- unit files
gy.CONFIG_FILE.write_text('[ollama_env]\nWEIRD = "50% \\"quoted\\" back\\\\slash"\n')
unit = gy.ollama_unit_text(gy.load_config())
assert 'Environment="WEIRD=50%% \\"quoted\\" back\\\\slash"' in unit, unit
assert "BindsTo=gpu-yield.service" in unit and "After=gpu-yield.service" in unit
ok("escapes %, quotes and backslashes in unit values; Ollama is bound to the watcher")

if shutil.which("systemd-analyze"):
    d = gy.HOME / "units"
    d.mkdir()
    (d / gy.WATCHER_UNIT).write_text(gy.watcher_unit_text().replace(str(gy.BIN_PATH), "/bin/true"))
    (d / gy.MANAGED_OLLAMA_UNIT).write_text(unit.replace(str(gy.OLLAMA_BIN), "/bin/true"))
    r = subprocess.run(["systemd-analyze", "verify", "--man=no", str(d / gy.MANAGED_OLLAMA_UNIT),
                        str(d / gy.WATCHER_UNIT)], capture_output=True, text=True)
    problems = [l for l in (r.stdout + r.stderr).splitlines()
                if gy.WATCHER_UNIT in l or gy.MANAGED_OLLAMA_UNIT in l]
    assert not problems, problems
    ok("systemd-analyze accepts both unit files")

# --- a broken config: Ollama off, clients told why, recovers when fixed
port = free_port()
gy.CONFIG_FILE.write_text(f'port = {port}\nmodel = "unterminated\n')
result = {}
t = threading.Thread(target=lambda: result.update(cfg=gy.load_config_or_wait()), daemon=True)
t.start()
wait(lambda: gy.STATUS_FILE.exists() and json.loads(gy.STATUS_FILE.read_text())["state"] == "config_error",
     msg="config_error status")
assert ("stop", gy.MANAGED_OLLAMA_UNIT) in calls
c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
c.request("GET", "/v1/models")
r = c.getresponse()
body = json.loads(r.read())
assert r.status == 503 and "config file has an error" in body["error"]["message"]
ok("broken config: Ollama is stopped and clients get a 503 explaining why (no crash loop)")

time.sleep(1.1)        # make sure the fix gets a new mtime
gy.CONFIG_FILE.write_text(f'port = {port}\nmodel = "gpt-oss:20b"\n')
t.join(10)
assert result.get("cfg", {}).get("model") == "gpt-oss:20b"
ok("starts normally as soon as the config is fixed")
print("config tests passed")
