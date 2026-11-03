"""1.13: shared-memory chips like the BC-250 — readings, fit check, defaults, doctor checks.
Everything runs against a simulated board (a fake /sys/class/drm and /proc files)."""
import os
import tempfile
import types
from pathlib import Path

from _util import load, ok

gy = load()
gy.os.geteuid = lambda: 1000
gy.user_systemctl = lambda *a: types.SimpleNamespace(returncode=0, stdout="", stderr="")
GiB = 2**30

# --- a simulated BC-250: 512 MB fixed GPU slice, the model in borrowed system memory (GTT)
drm = Path(tempfile.mkdtemp())
dev = drm / "card0/device"
dev.mkdir(parents=True)
files = {"vendor": "0x1002", "device": "0x13fe", "mem_info_vram_total": 512 * 2**20,
         "mem_info_vram_used": 100 * 2**20, "mem_info_gtt_total": 16 * GiB,
         "mem_info_gtt_used": 11 * GiB}
for k, v in files.items():
    (dev / k).write_text(f"{v}\n")
gy.DRM_DIR = drm
proc = {"/proc/meminfo": f"MemTotal: {int(15.5 * 2**20)} kB\nMemAvailable: {3 * 2**20} kB\n",
        "/proc/cmdline": "BOOT_IMAGE=/vmlinuz root=/dev/nvme0n1p2 amd_iommu=off\n",
        "/sys/module/ttm/parameters/pages_limit": "4194304\n", "/proc/swaps": "Filename\n"}
real_read = gy._read_text
gy._read_text = lambda p: proc[str(p)] if str(p) in proc else real_read(p)
real_glob = Path.glob

m = gy.memory_readings()
assert m["shared"] and m["gpu_used"] == 100 * 2**20 + 11 * GiB
assert gy.is_bc250() and gy.shared_memory_gpu()
assert gy.memory_line(m) == "shared memory 12.5 / 15.5 GB, 11.1 GB of it used by the GPU", gy.memory_line(m)
assert gy.shared_budget() == int(15.5 * GiB) - 3 * GiB
gy.Path = Path
ok("shared-memory chip: the GPU's use of the shared pool is read and shown as such")

# the model picker and the status page's "> VRAM" tag use the shared pool
assert gy.detect_memory() == (15.5, 15.5), gy.detect_memory()
w = gy.Watcher(dict(gy.DEFAULTS, port=1))
w.state = "gaming"                                   # no Ollama call needed for this
assert w.api_models()["vram_gb"] == 12.5
ok("model picker and model list measure against the shared pool (15.5 GB; 12.5 GB per model)")

# --- the Ollama service: 8-bit context storage by default on the BC-250
cfg = dict(gy.DEFAULTS, model="m")
assert 'Environment="OLLAMA_KV_CACHE_TYPE=q8_0"' in gy.ollama_unit_text(cfg)
cfg_f16 = dict(gy.DEFAULTS, model="m", ollama_env={"OLLAMA_KV_CACHE_TYPE": "f16"})
unit = gy.ollama_unit_text(cfg_f16)
assert 'OLLAMA_KV_CACHE_TYPE=f16' in unit and "q8_0" not in unit, "your own choice wins"
assert "MemoryMax" not in gy.ollama_unit_text(cfg), "no automatic RAM ceiling on a shared chip"
ok("BC-250: Ollama stores context at 8 bits unless you chose otherwise; no automatic RAM ceiling")


# --- the shared-memory fit check
class Stub:
    base = "http://stub"

    def __init__(self, size, share=60):
        self.size, self.share, self.calls = size, share, []

    def wait_ready(self, *a): return True
    def request(self, path, payload=None, **k):
        self.calls.append((path, payload))
        return {"models": [{"name": "big:1", "size": self.size, "digest": "d"},
                           {"name": "small:1", "size": 6 * GiB, "digest": "e"}]}
    def warm(self, name=None): self.calls.append(("warm", name))
    def loaded(self):
        return [{"name": "big:1", "size": self.size, "size_vram": self.size * self.share // 100}]
    def stop(self): return True
    def is_moe(self, m): return False


def warm(size, available):
    proc["/proc/meminfo"] = f"MemTotal: {int(15.5 * 2**20)} kB\nMemAvailable: {int(available * 2**20)} kB\n"
    w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1", alias=""))
    w.state, w.ollama = "ai", Stub(int(size * GiB))
    w._warm_inner(w.warm_gen)
    return w


gy.FIT_FILE.unlink(missing_ok=True)
w = warm(11.4, 3.0)                                   # Muse Glimmer AD-IQ3_XXS
assert w.warm_status.startswith("ready") and gy.last_fit()["model"] == "big:1"
assert not any("spilled" in e["text"] or "system RAM" in e["text"] for e in w.events), \
    "on a shared chip, 'part of it in system RAM' is normal, not a warning"
ok("a model that fits the shared pool loads, is remembered, and gets no misleading 'spilled' warning")

gy.save_fit("small:1", 65536)
w = warm(13.9, 3.0)                                   # AD-IQ3_M: over the line
assert ("warm", "big:1") not in w.ollama.calls, "refused before loading"
assert list(w.actions.queue) == [("model", "small:1")] and "too big for this PC's shared memory" in w.events[0]["text"]
ok("a model file too big for the shared pool is refused before it loads, and gpu-yield goes back")

w = warm(11.4, 0.4)                                   # loaded, but leaves Linux 0.4 GB
assert ("/api/generate", {"model": "big:1", "keep_alive": 0}) in w.ollama.calls
assert list(w.actions.queue) == [("model", "small:1")] and "left only" in w.events[0]["text"]
ok("a model that leaves too little memory for the rest of the PC is unloaded at once")

gy.Ollama = lambda c: Stub(int(13.9 * GiB))
assert gy.pull_and_load(dict(gy.DEFAULTS, port=1), "big:1") == "refused"
ok("gpu-yield model refuses a model too big for the shared pool, without loading it")

# --- programs holding memory count borrowed system memory on a shared chip
holders_proc = Path(tempfile.mkdtemp())
d = holders_proc / "500"
(d / "fd").mkdir(parents=True)
(d / "fdinfo").mkdir()
(d / "comm").write_text("ollama\n")
os.symlink("/dev/dri/renderD128", d / "fd" / "3")
(d / "fdinfo" / "3").write_text("drm-client-id:\t1\ndrm-memory-vram:\t1024 KiB\ndrm-memory-gtt:\t11534336 KiB\n")
h = gy.vram_holders(proc=str(holders_proc), pdev="x", gtt=True)
assert h and h[0][1] == "ollama" and h[0][2] == (1024 + 11534336) * 1024
assert gy.vram_holders(proc=str(holders_proc), pdev="x", gtt=False) == [], "VRAM alone is tiny"
ok("restart reports count each program's borrowed system memory on a shared chip")

# --- doctor's BC-250 checks
gy.run = lambda cmd, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="")   # nothing active
gy.shutil.which = lambda name: None
gy.os.uname = lambda: types.SimpleNamespace(release="6.17.9-200.fc43.x86_64")
cfg = dict(gy.DEFAULTS, model="glimmer", ollama_env={"OLLAMA_KV_CACHE_TYPE": "q4_0",
                                                     "HSA_OVERRIDE_GFX_VERSION": "10.3.0"})
proc["/sys/module/ttm/parameters/pages_limit"] = "1048576\n"


class Show:
    def request(self, path, payload=None, **k):
        return {"model_info": {"glimmer.attention.head_count_kv": 2}}


checks = gy.bc250_checks(cfg, Show())
bad = [msg for kind, msg in checks if kind == "bad"]
for words in ("known-bad range", "caps GPU memory", "HSA_OVERRIDE_GFX_VERSION",
              "no GPU clock governor", "isn't kept across reboots", "only 2 KV heads"):
    assert any(words in b for b in bad), (words, bad)
assert not any("IOMMU" in b for b in bad), "amd_iommu=off is on the command line"
assert any("bc250-check.sh" in msg for _, msg in checks)

gy.os.uname = lambda: types.SimpleNamespace(release="6.19.2-100.fc43.x86_64")
proc["/sys/module/ttm/parameters/pages_limit"] = "4194304\n"
gy.run = lambda cmd, **k: types.SimpleNamespace(returncode=0 if "cyan-skillfish-governor-smu" in cmd else 1,
                                                stdout="", stderr="")
checks = gy.bc250_checks(dict(gy.DEFAULTS, model="glimmer"), Show())
assert not any(k == "bad" and ("kernel" in m or "pages_limit" in m or "governor" in m) for k, m in checks)
assert any(k == "ok" and "governor running" in m for k, m in checks)
ok("doctor on a BC-250: kernel, memory limit, overrides, governor, journal and context-storage checks")

# --- nothing changes on an ordinary graphics card
(dev / "device").write_text("0x747e\n")              # an RX 7800 XT
(dev / "mem_info_vram_total").write_text(f"{16 * GiB}\n")
assert not gy.is_bc250() and not gy.shared_memory_gpu() and not gy.memory_readings()["shared"]
assert "OLLAMA_KV_CACHE_TYPE" not in gy.ollama_unit_text(dict(gy.DEFAULTS, model="m"))
ok("an ordinary graphics card is untouched: no shared-memory mode, no BC-250 defaults")
print("BC-250 tests passed")
