"""1.12: RAM ceiling and OOM priority for Ollama, and the fit guard on ROCm."""
import io
import types
import urllib.error

from _util import load, ok, wait

gy = load()
gy.os.geteuid = lambda: 1000
gy.user_systemctl = lambda *a: types.SimpleNamespace(returncode=0, stdout="", stderr="")

# --- RAM limit setting and the generated service
gy.total_ram = lambda: int(31.2 * 2**30)               # avignon
assert gy.ram_limit_bytes("auto") == 10 * 2**30
gy.total_ram = lambda: 8 * 2**30
assert gy.ram_limit_bytes("auto") == 4 * 2**30, "never below 4 GB"
assert gy.ram_limit_bytes("off") is None
assert gy.ram_limit_bytes("12") == gy.ram_limit_bytes("12G") == gy.ram_limit_bytes("12 GB") == 12 * 2**30
for bad in ("lots", "-3", "0", "12T", ""):
    assert gy.ram_limit_bytes(bad) is False, bad
assert "ollama_ram_limit" in gy.config_problem(dict(gy.DEFAULTS, ollama_ram_limit="lots"))
assert gy.config_problem(dict(gy.DEFAULTS, ollama_ram_limit="16")) is None
gy.total_ram = lambda: int(31.2 * 2**30)
unit = gy.ollama_unit_text(dict(gy.DEFAULTS, model="m"))
assert f"MemoryMax={10 * 2**30}\n" in unit and "MemorySwapMax=0\n" in unit and "OOMScoreAdjust=800\n" in unit
unit = gy.ollama_unit_text(dict(gy.DEFAULTS, model="m", ollama_ram_limit="off"))
assert "MemoryMax" not in unit and "MemorySwapMax=0" in unit and "OOMScoreAdjust=800" in unit
assert 'ollama_ram_limit = "auto"' in gy.CONFIG_TEMPLATE and "allow_partial_gpu = false" in gy.CONFIG_TEMPLATE
ok("Ollama's service: RAM ceiling (a third of RAM by default), no swap, stopped first if RAM runs out")


# --- the fit guard
class Stub:
    base = "http://stub"

    def __init__(self, share=100):
        self.share, self.calls, self.stopped = share, [], False

    def wait_ready(self, *a): return True
    def request(self, path, payload=None, **k):
        self.calls.append((path, payload))
        return {"models": [{"name": "big:1", "digest": "d"}, {"name": "small:1", "digest": "e"}]}
    def warm(self, name=None): self.calls.append(("warm", name))
    def loaded(self):
        return [{"name": "big:1", "size": 100, "size_vram": self.share, "context_length": 65536}]
    def stop(self):
        self.stopped = True
        return True
    def is_moe(self, m): return False


def warm(model="big:1", share=86, **cfg):
    w = gy.Watcher(dict(gy.DEFAULTS, port=1, model=model, alias="", **cfg))
    w.state = "ai"
    w.ollama = Stub(share)
    w._warm_inner(w.warm_gen)
    return w


rocm = dict(backend="rocm")
assert gy.backend_installed(dict(gy.DEFAULTS, **rocm)) is not False
gy.FIT_FILE.unlink(missing_ok=True)
w = warm(share=100, **rocm)
assert w.warm_status.startswith("ready") and gy.last_fit() == {"model": "big:1", "context_length": 65536}
ok("a model that fits completely is remembered as the last one that fit")

gy.save_fit("small:1", 65536)
w = warm(share=86, **rocm)
assert ("/api/generate", {"model": "big:1", "keep_alive": 0}) in w.ollama.calls, "must be unloaded"
assert list(w.actions.queue) == [("model", "small:1")]
assert any(e["kind"] == "error" and "only 86%" in e["text"] and "small:1" in e["text"] for e in w.events)
ok("ROCm: a model that spills is unloaded at once, and gpu-yield goes back to the last one that fit")

gy.save_fit("big:1", 32768)
w = warm(share=86, **rocm)
assert list(w.actions.queue) == [("context", 32768)], "a context change that spills is undone"
gy.save_fit("big:1", 65536)
w = warm(share=86, **rocm)
assert not list(w.actions.queue) and w.warm_status.startswith("failed:") and w.warm_retry == 300
assert "context_length 32768" in w.warm_status
ok("a context change that spills is undone; with nothing to go back to, it waits and says what to try")

gy.FIT_FILE.unlink()
w = warm(share=86, backend="vulkan")
assert not any(c[1] and c[1].get("keep_alive") == 0 for c in w.ollama.calls if c[0] == "/api/generate")
assert w.warm_status.startswith("ready") and "spilled" in w.events[0]["text"]
w = warm(share=86, allow_partial_gpu=True, **rocm)
assert w.warm_status.startswith("ready")
ok("Vulkan (or allow_partial_gpu = true): a model that spills stays loaded, with a warning")

# an app loading a model that doesn't fit gets it unloaded
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1", alias="", **rocm))
w.state, w.ollama = "ai", Stub()
w.start_warm = lambda: setattr(w, "rewarmed", True)
w.check_loaded([{"name": "big:1", "size": 100, "size_vram": 70, "context_length": 131072}])
assert ("/api/generate", {"model": "big:1", "keep_alive": 0}) in w.ollama.calls and w.rewarmed
assert "context 131072" in w.events[0]["text"] and "65536" in w.events[0]["text"]
ok("an app that reloads the model so it spills gets it unloaded, and gpu-yield reloads it properly")

# gpu-yield model refuses a model that doesn't fit, without switching
gy.Ollama = lambda cfg: Stub(80)
gy.subprocess.run = lambda *a, **k: types.SimpleNamespace(returncode=0)
assert gy.pull_and_load(dict(gy.DEFAULTS, port=1, **rocm), "big:1") is False
assert gy.pull_and_load(dict(gy.DEFAULTS, port=1, backend="vulkan"), "big:1") is True
ok("gpu-yield model won't switch to a model that doesn't fit (ROCm)")


# --- Ollama stopped for going over its RAM limit: reported, and no endless reload loop
journal = {"text": ""}
gy.run = lambda cmd, **k: types.SimpleNamespace(returncode=0, stdout=journal["text"], stderr="")
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1", alias=""))
w.state, w.ollama = "ai", Stub()
warms = []
w.start_warm = lambda: warms.append(1)
w.check_loaded([]); w.check_loaded([])                       # an ordinary unload: reload
assert warms == [1] and not any("RAM limit" in e["text"] for e in w.events)
journal["text"] = "gpu-yield-ollama.service: A process of this unit has been killed by the OOM killer.\n"
w.check_loaded([]); w.check_loaded([])
assert warms == [1, 1] and any("went over its RAM limit (10.0 GB)" in e["text"] for e in w.events)
w.check_loaded([]); w.check_loaded([])                       # again within 10 minutes
assert warms == [1, 1], "must stop reloading a model that keeps getting killed"
assert w.warm_status.startswith("failed:") and "keeps going over" in w.warm_status
ok("an out-of-RAM stop is reported; if it repeats, gpu-yield stops reloading and says why")
print("protection tests passed")
