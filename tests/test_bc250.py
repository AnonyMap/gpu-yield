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
assert gy.memory_line(m) == "shared memory 12.5 / 15.5 GB, 11.0 GB of it used by the GPU", gy.memory_line(m)
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

    def __init__(self, size, share=100):
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


def warm(size, available, share=100, alias=""):
    proc["/proc/meminfo"] = f"MemTotal: {int(15.5 * 2**20)} kB\nMemAvailable: {int(available * 2**20)} kB\n"
    w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1", alias=alias))
    w.state, w.ollama = "ai", Stub(int(size * GiB), share)
    w._warm_inner(w.warm_gen)
    return w


gy.FIT_FILE.unlink(missing_ok=True)
w = warm(11.4, 3.0)                                   # Muse Glimmer AD-IQ3_XXS
assert w.warm_status.startswith("ready") and gy.last_fit()["model"] == "big:1"
assert not any("spilled" in e["text"] or "system RAM" in e["text"] for e in w.events), \
    "on a shared chip, 'part of it in system RAM' is normal, not a warning"
ok("a model that fits the shared pool loads, is remembered, and gets no misleading 'spilled' warning")

gy.FIT_FILE.unlink()
w = warm(11.4, 3.0, share=60)
assert gy.last_fit() == {}, "layers on the CPU: not a fit"
assert any("runs on the CPU" in e["text"] and "pages_limit" in e["text"] for e in w.events)
ok("layers that Ollama put on the CPU are still warned about (pointing at the GPU memory limit)")

gy.save_fit("small:1", 65536)
w = warm(13.9, 3.0, alias="gpu-yield")                # AD-IQ3_M: over the line
assert ("warm", "big:1") not in w.ollama.calls, "refused before loading"
assert not any(c[0] == "/api/copy" for c in w.ollama.calls), "the apps' fixed name must not move to it"
assert list(w.actions.queue) == [("model", "small:1")] and "too big for this PC's shared memory" in w.events[0]["text"]
ok("a model file too big for the shared pool is refused before it loads, and gpu-yield goes back")

w = warm(11.4, 0.4)                                   # loaded, but leaves Linux 0.4 GB
assert ("/api/generate", {"model": "big:1", "keep_alive": 0}) in w.ollama.calls
assert list(w.actions.queue) == [("model", "small:1")] and "left only" in w.events[0]["text"]
ok("a model that leaves too little memory for the rest of the PC is unloaded at once")

# an app loading a bigger context (or another model) that runs the shared pool low
proc["/proc/meminfo"] = f"MemTotal: {int(15.5 * 2**20)} kB\nMemAvailable: {int(0.2 * 2**20)} kB\n"
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1", alias=""))
w.state, w.ollama = "ai", Stub(int(11.4 * GiB))
w.ollama.active = w.ollama.ready = lambda: True
w.ollama.loaded = lambda: [{"name": "big:1", "size": 1, "size_vram": 1, "context_length": 262144}]
w.start_warm = lambda: None
w.last_health, w.last_restart = 0, -1000
w.health(gy.time.monotonic())
assert ("/api/generate", {"model": "big:1", "keep_alive": 0}) in w.ollama.calls
assert "left the rest of the PC only" in w.events[0]["text"] and "262144" in w.events[0]["text"]
# its own model, at its own context, with memory gone low (something else grew)
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1", alias=""))
w.state, w.ollama = "ai", Stub(int(11.4 * GiB))
w.ollama.active = w.ollama.ready = lambda: True
w.ollama.loaded = lambda: [{"name": "big:1", "size": 1, "size_vram": 1, "context_length": 65536}]
w.last_health, w.last_restart = 0, -1000
w.health(gy.time.monotonic())
assert w.warm_status.startswith("failed: only") and w.warm_retry == 300 and not gy.PAUSE_FILE.exists()
proc["/proc/meminfo"] = f"MemTotal: {int(15.5 * 2**20)} kB\nMemAvailable: {3 * 2**20} kB\n"
ok("shared pool running low: an app's oversized load is unloaded; its own model waits 5 minutes")

# 1.13.2, Demeter's report: an app's *smaller* context, memory low for another reason. It
# must not be blamed, and the AI must not be paused, however often it happens.
def low_memory_watcher(loaded, available, own=12.5, **cfg):
    proc["/proc/meminfo"] = (f"MemTotal: {int(15.5 * 2**20)} kB\n"
                             f"MemAvailable: {int(available * 2**20)} kB\n")
    w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1", alias="gpu-yield", **cfg))
    w.state, w.ollama = "ai", Stub(int(11.4 * GiB))
    w.ollama.active = w.ollama.ready = lambda: True
    w.ollama.loaded = lambda: loaded
    w.start_warm = lambda: None
    w.own_load, w.last_restart = ("big:1", 65536, int(own * GiB)), -1000
    return w


def check(w):
    w.last_health = 0
    w.health(gy.time.monotonic())


gy.PAUSE_FILE.unlink(missing_ok=True)
smaller = [{"name": "big:1", "size": int(12.1 * GiB), "size_vram": int(12.1 * GiB),
            "context_length": 16384}]
w = low_memory_watcher(smaller, 0.6)
check(w)
assert not w.ollama.calls, "one low reading is not enough"
check(w)
assert ("/api/generate", {"model": "big:1", "keep_alive": 0}) in w.ollama.calls, "still unloaded"
assert "isn't the cause" in w.warm_status and "16384" in w.warm_status and "Using memory:" in \
    w.warm_status, w.warm_status
for _ in range(6):                                   # again and again: still no pause
    w.warm_failed_at = None
    check(w)
assert not gy.PAUSE_FILE.exists() and not w.block_reason and not w.client_spills
w = low_memory_watcher(smaller, 0.6)
check(w)
proc["/proc/meminfo"] = f"MemTotal: {int(15.5 * 2**20)} kB\nMemAvailable: {3 * 2**20} kB\n"
check(w)                                             # recovered: the count starts over
proc["/proc/meminfo"] = f"MemTotal: {int(15.5 * 2**20)} kB\nMemAvailable: {int(0.6 * 2**20)} kB\n"
check(w)
assert not w.ollama.calls, "readings must be in a row"
ok("an app's smaller context isn't blamed for low memory, never pauses the AI; one reading isn't enough")

# a load that really is bigger than ours: unloaded, then paused on a repeat, with remote resume
bigger = [{"name": "big:1", "size": int(13.6 * GiB), "size_vram": int(13.6 * GiB),
           "context_length": 131072}]
w = low_memory_watcher(bigger, 0.6, control_password="longenough", status_port=11435)
check(w); check(w)
assert "131072" in w.events[0]["text"] and not gy.PAUSE_FILE.exists()
check(w); check(w)
assert gy.PAUSE_FILE.exists() and "leave its context unset" in w.block_reason and \
    "Resume on http://" in w.block_reason, w.block_reason
gy.PAUSE_FILE.unlink()
assert "GPU" in gy.memory_users()
ok("a load bigger than gpu-yield's own still pauses on a repeat; the notice says how to resume remotely")

# review of 1.13.2: our own load is never blamed; unknown sizes blame nothing; stale readings
w = low_memory_watcher([], 0.6, own=8.0)              # own_size from a smaller app reload
ours_full = {"name": "gpu-yield:latest", "size": int(12.5 * GiB), "context_length": 65536}
assert not w.bigger_than_ours(ours_full), "gpu-yield's own model at its own context"
w.own_load = None
assert not w.bigger_than_ours({"name": "nomic-embed-text", "size": int(0.3 * GiB)})
assert w.bigger_than_ours({"name": "big:1", "size": 1, "context_length": 131072})
w = low_memory_watcher(smaller, 0.6)
w.low_memory_at = gy.time.monotonic() - 600           # a reading from before a game
check(w)
assert not w.ollama.calls, "an old low reading doesn't count toward two in a row"
real_gpu_fit = gy.guards_gpu_fit
gy.guards_gpu_fit = lambda c: True                    # an APU on ROCm: both checks apply
w = low_memory_watcher(smaller, 0.6)
check(w); check(w)
gy.guards_gpu_fit = real_gpu_fit
assert ("/api/generate", {"model": "big:1", "keep_alive": 0}) in w.ollama.calls
# own_size is recorded only from our own load at our own context
w = warm(11.4, 3.0)
assert w.own_size() == int(11.4 * GiB), w.own_load
proc["/proc/meminfo"] = f"MemTotal: {int(15.5 * 2**20)} kB\nMemAvailable: {3 * 2**20} kB\n"
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1", alias=""))
w.state, w.ollama = "ai", Stub(int(11.4 * GiB))
w.ollama.loaded = lambda: [{"name": "big:1", "size": int(11 * GiB), "size_vram": int(11 * GiB),
                            "context_length": 8192}]     # an app's request got in first
w._warm_inner(w.warm_gen)
assert w.own_size() is None
ok("our own load is never blamed; unknown sizes blame nothing; stale readings and ROCm APUs handled")

# --- 1.13.3: memory audit
unit = gy.ollama_unit_text(dict(gy.DEFAULTS, model="m"))
for line in ('LLAMA_ARG_CACHE_RAM=256', 'LLAMA_ARG_CTX_CHECKPOINTS=4', 'LLAMA_ARG_LOAD_MODE=dio'):
    assert line in unit, line
unit = gy.ollama_unit_text(dict(gy.DEFAULTS, model="m", ollama_env={"LLAMA_ARG_CACHE_RAM": "1024"}))
assert "LLAMA_ARG_CACHE_RAM=1024" in unit and "LLAMA_ARG_CACHE_RAM=256" not in unit
ok("shared chip: llama-server's RAM prompt cache capped, few context snapshots, direct-I/O loads")

# giving up after repeated out-of-memory stops must not leave the health checks switched off
w = gy.Watcher(dict(gy.DEFAULTS, port=1, model="big:1"))
w.warming = True
w.give_up_on_ram(w.warm_gen)
assert not w.warming and w.warm_retry == 10**9
assert "Linux keeps stopping Ollama" in w.warm_status and "ollama_ram_limit" not in w.warm_status
ok("giving up on a load leaves the memory checks running; no RAM-limit talk when there is none")

# nothing to compare with: say so, and blame only what's bigger than any model may take
other = [{"name": "qwen3:14b", "size": int(9 * GiB), "size_vram": int(9 * GiB), "context_length": 65536}]
w = low_memory_watcher(other, 0.3)
w.own_load = None
check(w)
assert "may be the cause" in w.warm_status and not w.client_spills, w.warm_status
huge = [{"name": "qwen3:32b", "size": int(13.5 * GiB), "size_vram": int(13.5 * GiB), "context_length": 65536}]
w = low_memory_watcher(huge, 0.3)
w.own_load = None
check(w)
assert ("qwen3:32b", 65536) in w.client_spills, "bigger than the shared budget: blamed"
w = low_memory_watcher(other, 0.3)
w.own_load = ("other:1", 65536, int(5 * GiB))        # from a different model: not comparable
assert w.own_size() is None
ok("low memory with no load of ours to compare: honest message; an oversized app load still blamed")

proc["/sys/module/ttm/parameters/page_pool_size"] = "0\n"
assert any(k == "note" and "ttm.page_pool_size=262144" in m for k, m in gy.bc250_checks(dict(gy.DEFAULTS)))
proc["/sys/module/ttm/parameters/page_pool_size"] = "262144\n"
assert not any("page_pool_size" in m for k, m in gy.bc250_checks(dict(gy.DEFAULTS)))
del proc["/sys/module/ttm/parameters/page_pool_size"]
ok("doctor notes TTM's pool of freed GPU memory, which Linux counts as in use")

gy.Ollama = lambda c: Stub(int(13.9 * GiB))
assert gy.pull_and_load(dict(gy.DEFAULTS, port=1), "big:1") == "refused"
ok("gpu-yield model refuses a model too big for the shared pool, without loading it")

# --- detection: a BC-250 with a bigger BIOS slice, and an iGPU next to another vendor's card
(dev / "mem_info_vram_total").write_text(f"{4 * GiB}\n")
assert gy.memory_readings()["shared"], "a BC-250 is shared at any BIOS setting"
(dev / "mem_info_vram_total").write_text(f"{512 * 2**20}\n")
(dev / "device").write_text("0x164e\n")                  # a Ryzen 7000 iGPU
other = drm / "card1/device"
other.mkdir(parents=True)
(other / "vendor").write_text("0x10de\n")               # NVIDIA
(other / "class").write_text("0x030000\n")
assert not gy.memory_readings()["shared"], "an iGPU next to an NVIDIA card isn't the AI's GPU"
import shutil as _sh
_sh.rmtree(drm / "card1")
(dev / "device").write_text("0x13fe\n")
ok("detection: a BC-250 with a 2-4 GB BIOS slice is shared; an iGPU beside an NVIDIA card isn't")

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

gy.os.uname = lambda: types.SimpleNamespace(release="6.18.5-200.fc43.x86_64")
assert any(k == "note" and "6.18.18+" in m for k, m in gy.bc250_checks(dict(gy.DEFAULTS)))
etc = Path(tempfile.mkdtemp())
(etc / "ttm.conf").write_text("options ttm pages_limit=3959290 page_pool_size=3959290\n")
real_path = gy.Path
gy.Path = lambda p, *a: real_path(str(etc) if p == "/etc/modprobe.d" else p, *a)
proc["/proc/cmdline"] = "root=/dev/x ttm.pages_limit=4194304 amd_iommu=off\n"
checks = gy.bc250_checks(dict(gy.DEFAULTS))
gy.Path = real_path
assert any(k == "bad" and "different values" in m and "3959290" in m for k, m in checks), checks
gov = Path(tempfile.mkdtemp()) / "config.toml"
gov.write_text("[frequency-range]\n# both values in MHz\nmin = 350\nmax = 1500\n")
real_conf = gy.Path
gy.Path = lambda p, *a: gov if str(p).endswith("cyan-skillfish-governor-smu/config.toml") else real_conf(p, *a)
checks = gy.bc250_checks(dict(gy.DEFAULTS))
gy.Path = real_conf
assert not any("MHz" in m for k, m in checks if k == "bad"), "a comment mentioning MHz is fine"
ok("doctor: 6.18 before .18 gets the note; pages_limit read with other options; MHz in a comment is fine")

# --- 1.13.1: Ollama must be allowed to use an integrated GPU, or it runs on the CPU
pci = Path(tempfile.mkdtemp())
def pci_gpus(*classes):
    for d in pci.glob("*"):
        (d / "class").unlink(); d.rmdir()
    for i, c in enumerate(classes):
        (pci / f"0000:0{i}:00.0").mkdir()
        (pci / f"0000:0{i}:00.0/class").write_text(c + "\n")
gy.PCI_DIR = pci
pci_gpus("0x030000", "0x020000", "0x060400")          # the board's GPU, a network card, a bridge
assert 'Environment="OLLAMA_IGPU_ENABLE=1"' in gy.ollama_unit_text(dict(gy.DEFAULTS, model="m"))
unit = gy.ollama_unit_text(dict(gy.DEFAULTS, model="m", ollama_env={"OLLAMA_IGPU_ENABLE": "0"}))
assert "OLLAMA_IGPU_ENABLE=0" in unit and "OLLAMA_IGPU_ENABLE=1" not in unit, "your own choice wins"
for value, off in (("0", True), ("f", True), ("False", True), (" 'false' ", True),
                   ("no", False), ("off", False), ("1", False), ("", False)):
    assert gy.igpu_turned_off(dict(gy.DEFAULTS, ollama_env={"OLLAMA_IGPU_ENABLE": value})) == off, \
        value                                         # Go's ParseBool: "no"/"off" read as on
ok("shared-memory chip: Ollama is told to use the integrated GPU (OLLAMA_IGPU_ENABLE=1)")

# Ollama's own log: what it found since its last start, and integrated GPUs it dropped
old_start = ('time=2026-11-03T19:00:00 level=INFO source=routes.go:1 msg="server config" env="map[]"\n'
             'time=2026-11-03T19:00:01 level=INFO source=types.go:60 msg="inference compute" id=0 '
             'library=Vulkan name=Vulkan0 description="old run" type=iGPU total="9 GiB" available="9 GiB"\n')
cpu_only = ('time=2026-11-03T20:40:00 level=INFO source=routes.go:1 msg="server config" '
            'env="map[OLLAMA_VULKAN:true]"\n'
            'time=2026-11-03T20:40:01 level=INFO source=runner.go:405 msg="dropping integrated GPU; '
            'to enable, set OLLAMA_IGPU_ENABLE=1" id=0 library=Vulkan compute=0.0 name=Vulkan0 '
            'description="AMD BC-250 (RADV GFX1013)" pci_id=0000:01:00.0\n'
            'time=2026-11-03T20:40:01 level=INFO source=types.go:60 msg="inference compute" id=cpu '
            'library=cpu compute="" name=cpu description=cpu libdirs=ollama driver="" pci_id="" '
            'type="" total="15.5 GiB" available="12.0 GiB"\n')
on_gpu = ('time=2026-11-03T20:50:00 level=INFO source=routes.go:1 msg="server config" env="map[]"\n'
          'time=2026-11-03T20:50:01 level=INFO source=types.go:60 msg="inference compute" id=0 '
          'library=Vulkan compute=0.0 name=Vulkan0 description="AMD BC-250 (RADV GFX1013)\x1b]0;x\x07" '
          'libdirs=ollama,vulkan driver=0.0 pci_id=0000:01:00.0 type=iGPU total="12.3 GiB" '
          'available="12.3 GiB"\n')
journal = {"text": ""}
real_run = gy.run
gy.run = lambda cmd, *a, **k: (types.SimpleNamespace(returncode=0, stdout=journal["text"], stderr="")
                               if cmd[0] == "journalctl" else real_run(cmd, *a, **k))
journal["text"] = old_start + cpu_only
found = gy.ollama_devices(gy.MANAGED_OLLAMA_UNIT)
assert [d["description"] for d in found["dropped"]] == ["AMD BC-250 (RADV GFX1013)"], found
assert [d["library"] for d in found["devices"]] == ["cpu"], "only the latest start counts"
assert gy.describe_devices(found) == ["CPU only"]


class CpuStub:
    def __init__(self, cfg): self.cfg, self.unit = cfg, cfg["ollama_unit"]
    def is_moe(self, m): return False


problem, msg = gy.placement_note(CpuStub(dict(gy.DEFAULTS)), "m", 0)
assert problem and "integrated" in msg and "env.OLLAMA_IGPU_ENABLE 1" in msg, msg
assert "--backend vulkan" not in msg
problem, msg = gy.placement_note(CpuStub(dict(gy.DEFAULTS, ollama_env={"OLLAMA_IGPU_ENABLE": "0"})), "m", 0)
assert "set to off" in msg, msg
journal["text"] = old_start                          # nothing dropped, Vulkan in use
problem, msg = gy.placement_note(CpuStub(dict(gy.DEFAULTS, backend="vulkan")), "m", 0)
assert "--backend vulkan" not in msg and "doctor" in msg, "no advice to switch to what's in use"
journal["text"] = cpu_only + on_gpu
found = gy.ollama_devices(gy.MANAGED_OLLAMA_UNIT)
assert not found["dropped"] and gy.describe_devices(found) == \
    ["Vulkan AMD BC-250 (RADV GFX1013)]0;x, iGPU: 12.3 GiB free of 12.3 GiB"], gy.describe_devices(found)
journal["text"] = ""
assert gy.ollama_devices(gy.MANAGED_OLLAMA_UNIT) is None
gy.run = real_run
r = load().run(["printf", "bad \\377 byte"])   # the real run(); this file stubs gy.run            # invalid UTF-8 from a runner's output
assert r.returncode == 0 and "\ufffd" in r.stdout, r
ok("Ollama's log: the GPUs it found, an ignored integrated GPU, and advice that fits")

# --- nothing changes on an ordinary graphics card
(dev / "device").write_text("0x747e\n")              # an RX 7800 XT
(dev / "mem_info_vram_total").write_text(f"{16 * GiB}\n")
assert not gy.is_bc250() and not gy.shared_memory_gpu() and not gy.memory_readings()["shared"]
assert "OLLAMA_KV_CACHE_TYPE" not in gy.ollama_unit_text(dict(gy.DEFAULTS, model="m"))
pci_gpus("0x030000", "0x030000")                    # a graphics card plus the processor's graphics
assert "OLLAMA_IGPU_ENABLE" not in gy.ollama_unit_text(dict(gy.DEFAULTS, model="m")), \
    "next to a graphics card, the processor's own graphics stay out"
pci_gpus("0x030000")                                 # an APU with 4 GB of BIOS graphics memory
(dev / "mem_info_vram_total").write_text(f"{4 * GiB}\n")
assert not gy.shared_memory_gpu()
assert 'OLLAMA_IGPU_ENABLE=1' in gy.ollama_unit_text(dict(gy.DEFAULTS, model="m")), \
    "the only GPU is admitted whatever its BIOS memory slice"
ok("an ordinary graphics card is untouched: no shared-memory mode, no BC-250 defaults")
print("BC-250 tests passed")
