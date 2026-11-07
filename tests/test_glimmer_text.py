"""extras/glimmer-text: listing, fit verdicts, choosing a build, resumable checked downloads,
the Ollama import, and its refusals. No network: Hugging Face and Ollama are stubbed."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _util import ok

HOME = Path(tempfile.mkdtemp(prefix="gt-home-"))
os.environ["HOME"] = str(HOME)
for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_MODELS"):
    os.environ.pop(k, None)
SCRIPT = Path(__file__).resolve().parents[1] / "extras" / "glimmer-text"
loader = importlib.machinery.SourceFileLoader("glimmer_text", str(SCRIPT))
spec = importlib.util.spec_from_loader("glimmer_text", loader)
gt = importlib.util.module_from_spec(spec)
loader.exec_module(gt)
gt.os.geteuid = lambda: 1000
gt.time.sleep = lambda s: None
GIB = gt.GIB


def attempt(fn, *a, _out=None, **k):
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(_out or io.StringIO()):
            fn(*a, **k)
    except SystemExit as e:
        return e.code or 0, err.getvalue()
    return 0, err.getvalue()


# --- the listing: language-model files only, named by build, smallest first
LISTING = [
    {"type": "file", "path": "README.md", "size": 100},
    {"type": "file", "path": "mmproj-Muse-Glimmer-30B-BF16.gguf", "size": 4 * GIB, "lfs": {"oid": "a" * 64, "size": 4 * GIB}},
    {"type": "file", "path": "Muse-Glimmer-30B-dflash-Q8_0.gguf", "size": 2 * GIB, "lfs": {"oid": "b" * 64, "size": 2 * GIB}},
    {"type": "file", "path": "Muse-Glimmer-30B-AD-IQ3_XXS.gguf", "size": int(12.2e9), "lfs": {"oid": "c" * 64, "size": int(12.2e9)}},
    {"type": "file", "path": "Muse-Glimmer-30B-AD-IQ2_M.gguf", "size": int(11.16e9), "lfs": {"oid": "d" * 64, "size": int(11.16e9)}},
    {"type": "file", "path": "Muse-Glimmer-30B-Q4_K_M.gguf", "size": int(19.1e9), "lfs": {"oid": "e" * 64, "size": int(19.1e9)}},
    {"type": "file", "path": "BF16/Muse-Glimmer-30B-BF16-00001-of-00002.gguf", "size": int(30e9), "lfs": {"oid": "f" * 64, "size": int(30e9)}},
    {"type": "file", "path": "BF16/Muse-Glimmer-30B-BF16-00002-of-00002.gguf", "size": int(25.7e9), "lfs": {"oid": "0" * 64, "size": int(25.7e9)}},
    {"type": "file", "path": "../evil.gguf", "size": 1},
]
entries = gt.quant_entries(LISTING)
assert [e["quant"] for e in entries] == ["AD-IQ2_M", "AD-IQ3_XXS", "Q4_K_M", "BF16"], [e["quant"] for e in entries]
bf16 = entries[-1]
assert bf16["split"] and bf16["size"] == int(55.7e9)
assert entries[1]["sha256"] == "c" * 64 and entries[1]["path"] == "Muse-Glimmer-30B-AD-IQ3_XXS.gguf"
assert gt.quant_name("bartowski_Muse-Glimmer-30B-IQ3_XXS.gguf") == "IQ3_XXS"
assert "original" in gt.describe("BF16") and gt.describe("Q4_K_M") == "standard 4-bit"
assert "AtomicChat" in gt.describe("AD-IQ3_XXS") and "3-bit" in gt.describe("AD-IQ3_XXS")
ok("lists only the language-model builds (no image part, no drafter, no ../), split builds grouped")

# --- this PC: a 16 GB card, a BC-250, or no AMD GPU
def fake_sysfs(vram, device="0x747e", mem_kb=32 * 2**20):
    root = Path(tempfile.mkdtemp())
    dev = root / "card1" / "device"
    dev.mkdir(parents=True)
    (dev / "vendor").write_text("0x1002\n")
    (dev / "device").write_text(device + "\n")
    (dev / "mem_info_vram_total").write_text(str(vram))
    (root / "meminfo").write_text(f"MemTotal:       {mem_kb} kB\n")
    return root, root / "meminfo"

drm, mi = fake_sysfs(16 * GIB)
dgpu = gt.gpu_info(drm, mi)
assert dgpu["kind"] == "dgpu" and dgpu["budget"] == int(14.5 * GIB)
drm, mi = fake_sysfs(512 * 2**20, gt.BC250_DEVICE, mem_kb=int(15.5 * 2**20))
apu = gt.gpu_info(drm, mi)
assert apu["kind"] == "apu" and apu["device"] == gt.BC250_DEVICE and apu["budget"] == int(13 * GIB)
assert gt.gpu_info(Path(tempfile.mkdtemp()), mi)["kind"] is None

extra = gt.context_bytes(65536, "f16") + gt.WORKING_SPACE
assert abs(gt.context_bytes(65536, "f16") - (65536 * 13 * 1024 + gt.CTX_SLIDING_BYTES)) < 2
assert gt.context_bytes(65536, "q8_0") < gt.context_bytes(65536, "f16")
v16 = {e["quant"]: e["verdict"] for e in gt.assess([dict(e) for e in entries], dgpu, extra)}
assert v16 == {"AD-IQ2_M": "fits", "AD-IQ3_XXS": "fits", "Q4_K_M": "won't fit", "BF16": "won't fit"}, v16
vapu = {e["quant"]: e["verdict"] for e in gt.assess([dict(e) for e in entries], apu, extra)}
assert vapu["AD-IQ2_M"] in ("fits", "tight") and vapu["Q4_K_M"] == "won't fit", vapu
assert gt.verdict(1, None) == "unknown"
ok("fit verdicts for a 16 GB card and a BC-250 (shared memory), and 'unknown' without an AMD GPU")

# --- gpu-yield's settings are used when present
(HOME / ".config/gpu-yield").mkdir(parents=True)
(HOME / ".config/gpu-yield/config.toml").write_text(
    'model = "qwen3.5:9b"\ncontext_length = 32768\nport = 11500\n\n[ollama_env]\nOLLAMA_KV_CACHE_TYPE = "q8_0"\n')
cfg = gt.gy_config()
assert gt.context_length(cfg) == 32768 and gt.context_length(cfg, 8192) == 8192
assert gt.kv_cache_type(cfg) == "q8_0" and gt.ollama_port(cfg) == 11500
assert gt.context_length({}) == 65536 and gt.ollama_port({}) == 11434
ok("uses gpu-yield's context, port and context-memory type when it's installed")

# --- choosing a build
assess16 = gt.assess([dict(e) for e in entries], dgpu, extra)
assert gt.find_quant(assess16, "ad-iq3_xxs")["quant"] == "AD-IQ3_XXS"
assert gt.find_quant(assess16, "Muse-Glimmer-30B-Q4_K_M.gguf")["quant"] == "Q4_K_M"
assert gt.find_quant(assess16, "nope") is None
assert gt.default_choice(assess16)["quant"] == "AD-IQ3_XXS", "the biggest one that fits"

gt.gpu_info = lambda: dgpu
gt.hf_api_list = lambda repo: LISTING
calls = []
gt.subprocess.run = lambda cmd, **k: calls.append(cmd) or types.SimpleNamespace(returncode=0)


def install(quant=None, interactive=False, out=None, **kw):
    args = types.SimpleNamespace(repo=gt.DEFAULT_REPO, context=None, quant=quant, name=None, dir=None,
                                 force=False, keep=False, no_load=False)
    for k, v in kw.items():
        setattr(args, k, v)
    return attempt(gt.cmd_install, args, interactive=interactive, _out=out)


code, err = install("Q4_K_M")
assert code == 1 and "won't fit" not in err and "more than this PC has" in err and "--force" in err
code, err = install("BF16", force=True)
assert code == 1 and "split into several files" in err
code, err = install(None)
assert code == 1 and "AD-IQ3_XXS" in err and "Choices" in err
code, err = install("AD-IQ9_ZZZ")
assert code == 1 and "no build called" in err
ok("refuses a build that won't fit (unless --force), split builds, unknown names; asks for a choice")

# --- names and repos are validated
code, err = attempt(gt.validate_repo, "../../etc")
assert code == 1
code, err = attempt(gt.validate_repo, "owner/name/extra")
assert code == 1
assert gt.validate_repo("bartowski/Muse-Glimmer-30B-GGUF")
code, err = install("AD-IQ2_M", name="Bad Name!")
assert code == 1 and "valid Ollama model name" in err
ok("repo and model names are checked; files come only from the repo's own listing")

# --- root refused
gt.os.geteuid = lambda: 0
code, err = attempt(gt.main, ["list"])
assert code == 1 and "normal user" in err
gt.os.geteuid = lambda: 1000
ok("refuses to run as root")

# --- download: resumes a partial file over HTTP Range, and checks the SHA256
BLOB = os.urandom(3 * 2**20 + 123)
SHA = hashlib.sha256(BLOB).hexdigest()
served = {"ranges": [], "drop_once": True}


class Files(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        rng = self.headers.get("Range")
        served["ranges"].append(rng)
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        body = BLOB[start:]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body)))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(BLOB) - 1}/{len(BLOB)}")
        self.end_headers()
        if served["drop_once"]:                      # a dropped connection mid-file
            served["drop_once"] = False
            self.wfile.write(body[:2**20])
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Files)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/file.gguf"
d = Path(tempfile.mkdtemp())
(d / "model.gguf.part").write_bytes(BLOB[:500_000])         # an earlier, interrupted run
(d / "model.gguf.glimmer-text").write_text("downloaded by glimmer-text\n")
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    final, ours = gt.download(url, d / "model.gguf", len(BLOB), SHA)
assert ours and final.read_bytes() == BLOB and not (d / "model.gguf.part").exists()
assert served["ranges"][0] == "bytes=500000-" and len(served["ranges"]) >= 2, served["ranges"]
ok("download continues a partial file, survives a dropped connection, and checks the SHA256")

served["drop_once"] = False
code, err = attempt(gt.download, url, d / "other.gguf", len(BLOB), "0" * 64)
assert code == 1 and "doesn't match" in err and (d / "other.gguf.part.bad").exists()
assert not (d / "other.gguf").exists()
ok("a file that doesn't match the checksum is set aside, never used")

# --- disk space: the file is briefly there twice
gt.free_space = lambda path: (int(20e9), 1)                 # same drive, 20 GB free
code, err = attempt(gt.check_disk, {"path": "x.gguf", "size": int(12.2e9)}, d, HOME / ".ollama/models")
assert code == 1 and "briefly there twice" in err
gt.free_space = lambda path: (int(30e9), 1)
assert attempt(gt.check_disk, {"path": "x.gguf", "size": int(12.2e9)}, d, HOME / ".ollama/models")[0] == 0
ok("refuses when the drive can't hold the download and Ollama's copy at once")

# --- the whole install: download, one-line Modelfile, ollama create, load through gpu-yield
calls.clear()
gt.check_ollama_ready = lambda port: None
made = {}
def fake_download(url_, final, size, sha):
    made["url"] = url_
    Path(final).write_bytes(b"GGUF")
    return Path(final), True
gt.download = fake_download
real_run = gt.subprocess.run
def run(cmd, **k):
    calls.append(cmd)
    if cmd[1:2] == ["create"]:
        made["modelfile"] = Path(cmd[-1]).read_text()
        made["env"] = k.get("env", {}).get("OLLAMA_HOST")
    return types.SimpleNamespace(returncode=0)
gt.subprocess.run = run
(HOME / ".local/opt/ollama/bin").mkdir(parents=True)
(HOME / ".local/opt/ollama/bin/ollama").write_text("")
(HOME / ".local/bin").mkdir(parents=True)
(HOME / ".local/bin/gpu-yield").write_text("")
gt.shutil.which = lambda name: None
out = io.StringIO()
code, err = install("ad-iq3_xxs", dir=str(d), out=out)
assert code == 0, err
dl = d / "Muse-Glimmer-30B-AD-IQ3_XXS.gguf"
assert made["url"].endswith("/AtomicChat/Muse-Glimmer-30B-GGUF/resolve/main/Muse-Glimmer-30B-AD-IQ3_XXS.gguf")
assert made["modelfile"] == f"FROM {dl.resolve()}\n" and made["env"] == "127.0.0.1:11500"
assert calls[0][:4] == [str(HOME / ".local/opt/ollama/bin/ollama"), "create", "muse-glimmer-text-ad-iq3_xxs", "-f"]
assert not Path(calls[0][4]).exists() and Path(calls[0][4]).parent != d, "a private, removed folder"
assert calls[1] == [str(HOME / ".local/bin/gpu-yield"), "model", "muse-glimmer-text-ad-iq3_xxs"]
assert not dl.exists(), "the download is deleted once Ollama has its own copy"
assert "gpu-yield model qwen3.5:9b" in out.getvalue(), "says how to switch back"
ok("install: one-line Modelfile (no image part), gpu-yield's Ollama, loaded via gpu-yield, cleans up")

# a failed import keeps the file
calls.clear()
gt.subprocess.run = lambda cmd, **k: types.SimpleNamespace(returncode=1)
code, err = install("AD-IQ2_M", dir=str(d), no_load=True)
assert code == 1 and "kept at" in err and (d / "Muse-Glimmer-30B-AD-IQ2_M.gguf").exists()
ok("a failed import keeps the downloaded file and says where")

# --- Ollama off because of gpu-yield: explained before downloading anything
runtime = Path(tempfile.mkdtemp())
os.environ["XDG_RUNTIME_DIR"] = str(runtime)
(runtime / "gpu-yield").mkdir()
(runtime / "gpu-yield/status.json").write_text('{"state": "gaming"}')
loader.exec_module(gt)                    # the real functions again (earlier tests stubbed some)
gt.os.geteuid = lambda: 1000
code, err = attempt(gt.check_ollama_ready, 11500)
assert code == 1 and "game is running" in err
(runtime / "gpu-yield/status.json").write_text('{"state": "paused"}')
code, err = attempt(gt.check_ollama_ready, 11500)
assert code == 1 and "gpu-yield resume" in err
ok("stops early, with the reason, when gpu-yield has Ollama off (game or pause)")

# --- remove only touches this script's models, never the one in use
code, err = attempt(gt.cmd_remove, types.SimpleNamespace(name="qwen3.5:9b"))
assert code == 1 and "only removes models it created" in err
# --- Hugging Face errors are messages, not tracebacks
import urllib.error
def raiser(exc):
    def f(*a, **k):
        raise exc
    return f
for exc, text in ((urllib.error.HTTPError("u", 404, "nf", None, None), "no public repo"),
                  (urllib.error.HTTPError("u", 429, "slow", None, None), "rate-limiting"),
                  (urllib.error.URLError(OSError("no route")), "couldn't reach")):
    gt.http_get = raiser(exc)
    code, err = attempt(gt.hf_api_list, "a/b")
    assert code == 1 and text in err and "Traceback" not in err, err
ok("Hugging Face errors (missing repo, rate limit, no network) give a message")

# --- without gpu-yield: loads through Ollama directly and reports where it landed
gt.gpu_yield_bin = lambda: None
replies = {"/api/generate": {}, "/api/ps": {"models": [{"name": "muse-glimmer-text-x:latest",
                                                        "size": 100, "size_vram": 80}]}}
gt.ollama_request = lambda port, path, payload=None, timeout=10: replies[path]
err = io.StringIO()
with contextlib.redirect_stderr(err):
    where = gt.load_model("muse-glimmer-text-x", 11434, 65536)
assert where == "loaded, 80% on the GPU" and "20% of it is outside GPU memory" in err.getvalue()
replies["/api/ps"]["models"][0]["size_vram"] = 100
assert gt.load_model("muse-glimmer-text-x", 11434, 65536) == "loaded, 100% on the GPU"
ok("without gpu-yield, it loads through Ollama and warns if any of it spilled")
# --- from the review: a fresh copy of the script, since the tests above replaced parts of it
spec2 = importlib.util.spec_from_loader("glimmer_text2", importlib.machinery.SourceFileLoader("glimmer_text2", str(SCRIPT)))
g2 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(g2)
g2.time.sleep = lambda s: None

# a finished download from an earlier run is reused (checked), not downloaded again
d2 = Path(tempfile.mkdtemp())
(d2 / "model.gguf").write_bytes(BLOB)
served["ranges"].clear()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    assert g2.download(url, d2 / "model.gguf", len(BLOB), SHA) == (d2 / "model.gguf", False)
assert served["ranges"] == [], "must not download it again"
g2.free_space = lambda path: (int(20e9), 1)                  # 20 GB free, 12.2 GB file already here
(d2 / "x.gguf").write_bytes(b"")
os.truncate(d2 / "x.gguf", int(12.2e9))
assert attempt(g2.check_disk, {"path": "x.gguf", "size": int(12.2e9)}, d2, HOME / ".ollama/models")[0] == 0
ok("a rerun after a failed import reuses the finished download and counts it in the disk check")

# a full disk stops at once, with the partial file kept
class Full(io.BytesIO):
    status = 200
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): pass
g2.http_get = lambda *a, **k: Full(b"x" * 100)
real_open = open
def full_open(path, mode="r", *a, **k):
    if "b" in mode and str(path).endswith(".part"):
        raise OSError(28, "No space left on device")
    return real_open(path, mode, *a, **k)
g2.open = full_open
g2.open_part = lambda part, append: (_ for _ in ()).throw(
    g2.LocalFileError(OSError(28, "No space left on device")))
code, err = attempt(g2.download, url, d2 / "full.gguf", 100, "a" * 64)
del g2.open
assert code == 1 and "disk is full" in err and "retrying" not in err, err
ok("a full disk stops the download at once instead of retrying it as a network drop")

# long listings come in pages
pages = {"first": ([LISTING[3]], '<https://huggingface.co/api/models/a/b/tree/main?cursor=2>; rel="next"'),
         "https://huggingface.co/api/models/a/b/tree/main?cursor=2": ([LISTING[4]], "")}
class Page(io.BytesIO):
    def __init__(self, body, link):
        super().__init__(body)
        self.headers = {"Link": link}
    def __enter__(self): return self
    def __exit__(self, *a): pass
def paged(url_, *a, **k):
    items, link = pages.get(url_, pages["first"])
    return Page(gt.json.dumps(items).encode(), link)
g2.http_get = paged
assert [e["path"] for e in g2.hf_api_list("a/b")] == [LISTING[3]["path"], LISTING[4]["path"]]
ok("follows Hugging Face's next-page links for long file lists")

# a BC-250 with a bigger BIOS slice is still shared memory, and plans for q8_0 context
drm, mi = fake_sysfs(4 * GIB, g2.BC250_DEVICE, mem_kb=int(11.5 * 2**20))
info = g2.gpu_info(drm, mi)
assert info["kind"] == "apu"
assert g2.kv_cache_type({"model": "m"}, g2.BC250_DEVICE) == "q8_0"
assert g2.kv_cache_type({}, g2.BC250_DEVICE) == "f16", "without gpu-yield, Ollama's own default"
assert g2.kv_cache_type({"ollama_env": {"OLLAMA_KV_CACHE_TYPE": "f16"}}, g2.BC250_DEVICE) == "f16"
ok("BC-250 at any BIOS setting counts as shared memory, and plans for gpu-yield's q8_0 default")
# --- 1.13.4 security audit
g3spec = importlib.util.spec_from_loader("glimmer_text3", importlib.machinery.SourceFileLoader("glimmer_text3", str(SCRIPT)))
g3 = importlib.util.module_from_spec(g3spec); g3spec.loader.exec_module(g3)
g3.time.sleep = lambda s: None
d3 = Path(tempfile.mkdtemp())
# a file of yours with the build's name is never overwritten or deleted
(d3 / "model.gguf").write_bytes(b"my own file")
code, err = attempt(g3.download, url, d3 / "model.gguf", len(BLOB), SHA)
assert code == 1 and "isn't this build" in err and (d3 / "model.gguf").read_bytes() == b"my own file"
# a partial file this script didn't start is left alone
(d3 / "other.gguf.part").write_bytes(b"someone else's")
code, err = attempt(g3.download, url, d3 / "other.gguf", len(BLOB), SHA)
assert code == 1 and "wasn't started by glimmer-text" in err
# a planted link isn't followed when writing
victim = d3 / "victim"; victim.write_bytes(b"precious")
(d3 / "linked.gguf.glimmer-text").write_text("x")
(d3 / "linked.gguf.part").symlink_to(victim)
served["drop_once"] = False
with contextlib.redirect_stdout(io.StringIO()):
    code, err = attempt(g3.download, url, d3 / "linked.gguf", len(BLOB), SHA)
assert victim.read_bytes() == b"precious" and code == 1 and "couldn't write" in err, err
# no checksum, no download
code, err = attempt(g3.download, url, d3 / "nosum.gguf", len(BLOB), "")
assert code == 1 and "no checksum" in err
ok("never overwrites or deletes a file it didn't download, doesn't follow planted links, needs a checksum")

# names from the listing: plain file names only (no line breaks or escape codes)
odd = [{"type": "file", "path": n, "size": 10, "lfs": {"oid": "a" * 64, "size": 10}} for n in (
    "x\nSYSTEM evil\nM-Q4_K_M.gguf", "\x1b]0;title\x07M-Q4_K_M.gguf", "a" * 300 + "-Q4_K_M.gguf",
    "sub/ok-Q4_K_M.gguf", "-dash-Q4_K_M.gguf")]
assert [e["path"] for e in g3.quant_entries(odd)] == ["sub/ok-Q4_K_M.gguf"]
ok("file names with line breaks, escape codes or odd characters are left out of the listing")

# remove: a tag doesn't get round the in-use check
g3.gy_config = lambda: {"model": "muse-glimmer-text-q4_k_m"}
code, err = attempt(g3.cmd_remove, types.SimpleNamespace(name="muse-glimmer-text-q4_k_m:latest"))
assert code == 1 and "current model" in err
ok("remove refuses gpu-yield's current model with or without a tag")

# local write errors aren't retried as network drops
g3.open_part = lambda part, append: (_ for _ in ()).throw(g3.LocalFileError(OSError(13, "Permission denied")))
(d3 / "ro.gguf.glimmer-text").write_text("x")
served["ranges"].clear()
code, err = attempt(g3.download, url, d3 / "ro.gguf", len(BLOB), SHA)
assert code == 1 and "Permission denied" in err and len(served["ranges"]) == 1, (err, served["ranges"])
ok("a folder it can't write to stops at once with the reason")
print("glimmer-text tests passed")
