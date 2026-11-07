"""1.7: controls on the status page (password, model switch, context, downloads, delete,
pause), Hugging Face/Ollama name handling, and Wake on Wireless LAN detection."""
import http.client
import json
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _util import free_port, load, ok, wait

gy = load()
OLLAMA_PORT, PAGE_PORT = free_port(), free_port()
ollama = {"models": [{"name": "gpt-oss:20b", "size": 13_000_000_000, "digest": "d-gptoss"},
                     {"name": "qwen3.5:9b", "size": 6_600_000_000, "digest": "d-qwen9"}],
          "loaded": [], "generated": [], "deleted": [], "pull_delay": 0.05, "aliases": {}}


class FakeOllama(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, body, code=200):
        d = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def do_GET(self):
        if self.path == "/api/version":
            return self._json({"version": "fake"})
        if self.path == "/api/tags":     # like Ollama, a copy shows up as its own entry
            digest = {m["name"]: m.get("digest") for m in ollama["models"]}
            copies = [{"name": a + ":latest", "size": 1, "digest": digest.get(src, "d-" + src)}
                      for a, src in ollama["aliases"].items()]
            return self._json({"models": ollama["models"] + copies})
        if self.path == "/api/ps":
            return self._json({"models": ollama["loaded"]})
        self._json({"error": "nope"}, 404)

    def do_DELETE(self):
        name = self._body()["model"]
        ollama["deleted"].append(name)
        ollama["aliases"].pop(name.split(":")[0], None)
        ollama["models"] = [m for m in ollama["models"] if m["name"] != name]
        self._json({})

    def do_POST(self):
        req = self._body()
        if self.path == "/api/copy":
            if not any(m["name"] == req["source"] for m in ollama["models"]):
                return self._json({"error": "model not found"}, 404)
            ollama["aliases"][req["destination"]] = req["source"]
            return self._json({})
        if self.path == "/api/generate":
            if req.get("prompt"):                        # a speed test, not a load
                ollama.setdefault("speed_tests", []).append(req["model"])
                return self._json({"prompt_eval_count": 2000, "prompt_eval_duration": 2_000_000_000,
                                   "eval_count": 128, "eval_duration": 4_000_000_000})
            ollama["generated"].append((ollama["aliases"].get(req["model"], req["model"]),
                                        req["options"]["num_ctx"]))
            ollama["loaded"] = [{"name": req["model"], "size": 1, "size_vram": 1,
                                 "context_length": req["options"]["num_ctx"]}]
            return self._json({"done": True})
        if self.path == "/api/pull":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def chunk(obj):
                d = (json.dumps(obj) + "\n").encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(d), d))
                self.wfile.flush()
            try:
                chunk({"status": "pulling manifest"})
                for done in range(0, 101, 10):
                    time.sleep(ollama["pull_delay"])
                    chunk({"status": "pulling abc", "digest": "sha256:abc",
                           "total": 1000, "completed": done * 10})
                chunk({"status": "success"})
                self.wfile.write(b"0\r\n\r\n")
                ollama["models"].append({"name": req["model"], "size": 1000, "digest": "d-" + req["model"]})
            except (BrokenPipeError, ConnectionResetError):
                pass                       # the client cancelled
            return
        self._json({"error": "nope"}, 404)


srv = ThreadingHTTPServer(("127.0.0.1", OLLAMA_PORT), FakeOllama)
threading.Thread(target=srv.serve_forever, daemon=True).start()


class AlwaysUp(gy.Ollama):
    def active(self): return True
    def start(self): ollama.setdefault("starts", 0); ollama["starts"] += 1; return True
    def stop(self): ollama.setdefault("stops", 0); ollama["stops"] += 1; return True


gy.Ollama = AlwaysUp
gy.ensure_ollama_unit = lambda cfg: False
gy.CONFIG_DIR.mkdir(parents=True)
gy.CONFIG_FILE.write_text(gy.CONFIG_TEMPLATE)
gy.set_config_values({"model": "gpt-oss:20b", "port": OLLAMA_PORT, "status_port": PAGE_PORT,
                      "poll_seconds": 0.5})
w = gy.Watcher(gy.load_config())
w.standin.start = lambda: True     # the fake Ollama keeps the port; test_watcher covers the stand-in
w.standin.stop = lambda: None
threading.Thread(target=w.run, daemon=True).start()
wait(lambda: w.state == "ai" and w.warm_status.startswith("ready"), msg="initial load")


def call(method, path, body=None, password=None, ctype="application/json"):
    c = http.client.HTTPConnection("127.0.0.1", PAGE_PORT, timeout=10)
    headers = {"Content-Type": ctype} if body is not None else {}
    if password is not None:
        headers["X-GPU-Yield-Password"] = password
    c.request(method, path, body=json.dumps(body) if isinstance(body, dict) else body, headers=headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        data = json.loads(data)
    except ValueError:
        data = data.decode()
    return r.status, data, dict(r.getheaders())


# --- locked by default
code, body, _ = call("POST", "/api/auth", {}, "anything")
assert code == 403 and "control_password" in body["error"], body
assert call("GET", "/status")[1]["controls"] is False
ok("without a control password, every control is refused and the page says how to turn them on")

gy.set_config_values({"control_password": "hunter2"})   # as `gpu-yield config` would
w.cfg["control_password"] = "hunter2"                    # (that restarts the watcher; here we skip it)
code, body, _ = call("POST", "/api/auth", {}, "wrong")
assert code == 403 and body["error"] == "Wrong password"
assert call("POST", "/api/auth", {}, "hunter2")[0] == 200
assert call("POST", "/api/pause", {"paused": True})[0] == 403, "no password header"
ok("wrong passwords are refused; the right one unlocks")

# Guessing: a limit per device, however many connections it guesses on at once (up to
# the 6 a browser opens per site; more from one device are refused outright)
from concurrent.futures import ThreadPoolExecutor
w.failures.clear()
with ThreadPoolExecutor(5) as pool:
    codes = list(pool.map(lambda i: call("POST", "/api/auth", {}, f"guess{i}")[0], range(40)))
assert codes.count(403) == w.MAX_FAILURES and codes.count(429) == 40 - w.MAX_FAILURES, codes
code, body, _ = call("POST", "/api/auth", {}, "hunter2")
assert code == 429 and "Try again in" in body["error"], "even the right password waits out a lockout"
w.failures.clear()
assert call("POST", "/api/auth", {}, "hunter2")[0] == 200
for i in range(gy.Watcher.MAX_FAILURES):
    w.api("auth", {}, f"guess{i}", client="10.0.0.66")
assert w.api("auth", {}, "hunter2", client="10.0.0.66")[0] == 429
assert w.api("auth", {}, "hunter2", client="10.0.0.5")[0] == 200, "another device isn't locked out"
first = w.failures["10.0.0.66"]["until"]
w.failures["10.0.0.66"]["until"] = 0
for i in range(gy.Watcher.MAX_FAILURES):
    w.api("auth", {}, f"again{i}", client="10.0.0.66")
assert w.failures["10.0.0.66"]["locks"] == 2, "a repeat offender waits twice as long"
w.failures.clear()
ok("password guessing: 10 wrong tries lock that device out (doubling each time), not everyone")

# DNS rebinding: requests under a foreign host name are refused
def call_host(path, host, body=None):
    c = http.client.HTTPConnection("127.0.0.1", PAGE_PORT, timeout=10)
    c.putrequest("POST" if body is not None else "GET", path, skip_host=True)
    c.putheader("Host", host)
    if body is not None:
        data = json.dumps(body).encode()
        c.putheader("Content-Type", "application/json"); c.putheader("Content-Length", str(len(data)))
        c.putheader("X-GPU-Yield-Password", "hunter2"); c.endheaders(data)
    else:
        c.endheaders()
    r = c.getresponse(); r.read(); c.close()
    return r.status
me = gy.socket.gethostname().split(".")[0]
assert call_host("/api/auth", "evil.example:11435", {}) == 421
assert call_host("/api/models", "evil.example") == 421
assert call_host("/api/auth", f"{me}.home:{PAGE_PORT}", {}) == 200
assert call_host("/api/auth", f"{me.upper()}", {}) == 200
assert call_host("/api/auth", "192.168.1.23", {}) == 200 and call_host("/api/auth", "[fd00::5]:11435", {}) == 200
assert call_host("/status", "evil.example") == 200, "the read-only status stays public"
def status_as(host, path="/status"):
    c = http.client.HTTPConnection("127.0.0.1", PAGE_PORT, timeout=5)
    c.putrequest("GET", path, skip_host=True); c.putheader("Host", host); c.endheaders()
    r = c.getresponse(); body = r.read().decode(); c.close()
    return body
public = json.loads(status_as("evil.example"))
assert "ai_available" in public and "state" in public, public
for secret in ("mac", "host", "games", "events", "memory", "model"):
    assert secret not in public, (secret, public)
assert "details are hidden" in public["notice"].lower()
assert '"mac"' in status_as(f"{me}.home") or "mac" in json.loads(status_as(f"{me}.home"))
page = status_as("evil.example", "/")
import re as _re
embedded = json.loads(_re.search(r'<script id="initial" type="application/json">(.*?)</script>', page, _re.S).group(1))
assert set(embedded) <= set(gy.PUBLIC_FIELDS) | {"notice"}, embedded
ok("requests under a host name that isn't this PC's are refused (DNS rebinding); "
   "status under such a name says only whether the AI can be used")

# deeply nested JSON: a 400, not a dropped connection and a traceback
c = http.client.HTTPConnection("127.0.0.1", PAGE_PORT, timeout=5)
data = b"[" * 8000 + b"]" * 8000
c.putrequest("POST", "/api/auth", skip_host=True); c.putheader("Host", "127.0.0.1")
c.putheader("Content-Type", "application/json"); c.putheader("Content-Length", str(len(data)))
c.endheaders(data); r = c.getresponse(); r.read(); c.close()
assert r.status == 400, r.status
ok("absurdly nested JSON gets a 400")

# Any password characters work (the page percent-encodes them)
import urllib.parse
gy.set_config_values({"control_password": "пароль-ключ"}); w.cfg["control_password"] = "пароль-ключ"
assert call("POST", "/api/auth", {}, urllib.parse.quote("пароль-ключ"))[0] == 200
gy.set_config_values({"control_password": "hunter2"}); w.cfg["control_password"] = "hunter2"
ok("passwords with non-English characters work")

# Bad input gets an answer, never a dropped connection
for bad in ({"context_length": float("inf")}, {"context_length": "big"}, {"context_length": True}):
    assert call("POST", "/api/context", json.dumps(bad).replace("Infinity", "1e999"), "hunter2")[0] == 400, bad
assert call("POST", "/api/model", {"name": 5}, "hunter2")[0] == 400
assert call("POST", "/api/pull", {"name": ["x"]}, "hunter2")[0] == 400
ok("malformed requests get a clear 400")

assert call("POST", "/api/auth", "{}", "hunter2", ctype="text/plain")[0] == 415
assert call("OPTIONS", "/api/auth")[0] == 405
_, _, h = call("GET", "/status")
assert "Access-Control-Allow-Origin" not in h and h.get("X-Frame-Options") == "DENY"
_, _, h = call("POST", "/api/auth", {}, "hunter2")
assert "Access-Control-Allow-Origin" not in h
assert call("POST", "/api/auth", "x" * 20000, "hunter2")[0] == 413
assert call("POST", "/api/nope", {}, "hunter2")[0] == 404
ok("cross-site protection: JSON only, no CORS anywhere, no framing, size limit, unknown actions")

code, body, _ = call("GET", "/api/models")
assert [m["name"] for m in body["models"]] == ["gpt-oss:20b", "qwen3.5:9b"] and body["available"]
code, page, _ = call("GET", "/")
assert code == 200 and '"controls": true' in page and "gpt-oss:20b" in page
assert body["models_dir"] == "~/.ollama/models" and body["disk_free"] > 0
ok("models list and page served; free space is for the drive holding the models")

# OLLAMA_MODELS moves the model folder; free space follows it (even before it exists)
import tempfile, shutil as _sh
other = tempfile.mkdtemp(prefix="gy-models-")
cfg2 = dict(w.cfg, ollama_env={"OLLAMA_MODELS": other + "/not/yet/created"})
assert gy.models_dir(cfg2) == gy.Path(other + "/not/yet/created")
assert gy.models_free_space(gy.models_dir(cfg2)) == _sh.disk_usage(other).free
assert gy.tilde(gy.HOME / "x/y") == "~/x/y" and gy.tilde("/mnt/games/models") == "/mnt/games/models"
ok("a custom OLLAMA_MODELS folder is measured instead of the default")

# --- pause / resume
assert call("POST", "/api/pause", {"paused": True}, "hunter2")[0] == 200
wait(lambda: w.state == "paused", msg="paused")
assert w.snapshot()["paused"] is True
assert call("POST", "/api/pause", {"paused": False}, "hunter2")[0] == 200
wait(lambda: w.state == "ai" and w.warm_status.startswith("ready"), msg="resumed")
ok("pause and resume from the page")

# --- switch model
code, body, _ = call("POST", "/api/model", {"name": "llama-not-here:1b"}, "hunter2")
assert code == 400 and "isn't downloaded" in body["error"]
code, body, _ = call("POST", "/api/model", {"name": "qwen3.5:9b"}, "hunter2")
assert code == 202, body
wait(lambda: ollama["generated"][-1][0] == "qwen3.5:9b" and w.warm_status.startswith("ready"), msg="switch")
assert gy.load_config()["model"] == "qwen3.5:9b"
ok("switching model saves it to the config and loads it, without restarting Ollama")

# --- context
stops = ollama.get("stops", 0)
assert call("POST", "/api/context", {"context_length": 99}, "hunter2")[0] == 400
assert call("POST", "/api/context", {"context_length": 32768}, "hunter2")[0] == 202
wait(lambda: ollama["generated"][-1] == ("qwen3.5:9b", 32768) and w.warm_status.startswith("ready"), msg="ctx")
assert gy.load_config()["context_length"] == 32768 and ollama["stops"] > stops
ok("changing context saves it, restarts Ollama with it, and reloads the model")

# --- download with progress, then use it
code, body, _ = call("POST", "/api/pull", {"name": "https://huggingface.co/bartowski/Tiny-GGUF/tree/main",
                                           "use": True}, "hunter2")
assert code == 202, body
seen = []
wait(lambda: seen.append(json.loads(json.dumps(w.snapshot()["downloads"]))) or
     any(d["state"] == "done" for d in seen[-1]), msg="download done")
assert any(d and d[0]["state"] == "running" and 0 < d[0]["completed"] < d[0]["total"] for d in seen), \
    "progress was never visible"
assert seen[-1][0]["name"] == "hf.co/bartowski/Tiny-GGUF"
wait(lambda: gy.load_config()["model"] == "hf.co/bartowski/Tiny-GGUF", msg="switched to download")
ok("download from a pasted Hugging Face URL shows progress, then switches to it")

ollama["pull_delay"] = 0.5
assert call("POST", "/api/pull", {"name": "qwen3.5:4b"}, "hunter2")[0] == 202
assert call("POST", "/api/pull", {"name": "qwen3.5:4b"}, "hunter2")[0] == 409
time.sleep(0.8)
assert call("POST", "/api/cancel", {"name": "qwen3.5:4b"}, "hunter2")[0] == 200
wait(lambda: w.downloads["qwen3.5:4b"]["state"] == "cancelled", timeout=10, msg="cancel")
assert not any(m["name"] == "qwen3.5:4b" for m in ollama["models"])
ok("a download can be cancelled; starting the same one twice is refused")

# --- delete
code, body, _ = call("POST", "/api/delete", {"name": "hf.co/bartowski/Tiny-GGUF"}, "hunter2")
assert code == 409 and "in use" in body["error"]
assert call("POST", "/api/delete", {"name": "gpt-oss:20b"}, "hunter2")[0] == 200
assert ollama["deleted"] == ["gpt-oss:20b"]
ok("delete removes a model, but never the one in use")

# --- downloads need Ollama running
w.state = "gaming"
code, body, _ = call("POST", "/api/pull", {"name": "qwen3.5:2b"}, "hunter2")
assert code == 409 and "game" in body["error"]
w.state = "ai"
ok("downloads are refused while a game is running, with a reason")

# --- the fixed name for apps
assert "gpu-yield" in ollama["aliases"] and ollama["aliases"]["gpu-yield"] == gy.load_config()["model"]
names = [m["name"] for m in call("GET", "/api/models")[1]["models"]]
assert not any(n.startswith("gpu-yield") for n in names), "the fixed name isn't listed as a model"
assert call("POST", "/api/delete", {"name": "gpu-yield"}, "hunter2")[0] == 409
assert call("POST", "/api/model", {"name": "gpu-yield"}, "hunter2")[0] == 400
assert w.snapshot()["alias"] == "gpu-yield" and w.snapshot()["loaded_name"] == "gpu-yield"
ok("the fixed name follows the loaded model, isn't listed as a model, can't be deleted or chosen")

# --- speed test
code, body, _ = call("POST", "/api/speed", {}, "hunter2")
assert code == 202, body
wait(lambda: w.snapshot()["speed"]["state"] == "done", msg="speed test")
res = w.snapshot()["speed"]["result"]
assert (res["read_tps"], res["write_tps"], res["prompt_tokens"]) == (1000.0, 32.0, 2000), res
assert ollama["speed_tests"][-1] == "gpu-yield", "tested under the loaded name, so no reload"
assert w.snapshot()["speed_results"][0]["model"] == gy.load_config()["model"]
assert gy.load_speed_results()[0]["read_tps"] == 1000.0, "results are kept on disk"
p1, p2 = gy.speed_prompt(), gy.speed_prompt()
assert p1 != p2 and p1.splitlines()[1:] == p2.splitlines()[1:], "fresh first line, same notes"
w.state = "gaming"
assert call("POST", "/api/speed", {}, "hunter2")[0] == 409
w.state = "ai"
ok("speed test: reading and writing speeds, kept for comparison, refused during a game")

# --- recent events
texts = [e["text"] for e in w.snapshot()["events"]]
assert any("speed test of" in x for x in texts) and any("loaded" in x for x in texts), texts
assert all({"at", "text", "kind"} <= set(e) for e in w.snapshot()["events"])
w.event("model load failed: boom")
assert w.snapshot()["events"][0]["kind"] == "error"
ok("recent events are recorded, newest first, with errors marked")

# --- real memory readings
import tempfile
drm = gy.Path(tempfile.mkdtemp())
G = 2**30
for card, (total, used, gtt) in {"card0": (512e6, 100e6, 0), "card1": (16 * G, 15.1 * G, 2.3 * G)}.items():
    d = drm / card / "device"; d.mkdir(parents=True)
    (d / "mem_info_vram_total").write_text(str(int(total))); (d / "mem_info_vram_used").write_text(str(int(used)))
    (d / "mem_info_gtt_used").write_text(str(int(gtt)))
gy.DRM_DIR = drm
m = gy.memory_readings()
assert m["vram_total"] == 16 * G and m["gtt_used"] == int(2.3 * G) and m["ram_total"] > 0, m
line = gy.memory_line(m)
assert line.startswith("GPU 15.1 / 16.0 GB · RAM") and "+2.3 GB of RAM mapped for the GPU" in line, line
assert w.snapshot()["memory"]["vram_used"] == int(15.1 * G)
ok("memory readings come from the driver: the biggest GPU's VRAM, RAM mapped for it, and RAM")

# --- names
for given, want in (("qwen3.5:9b", "qwen3.5:9b"), ("https://ollama.com/library/qwen3.5:9b", "qwen3.5:9b"),
                    ("ollama.com/someuser/model", "someuser/model"),
                    ("hf.co/bartowski/X-GGUF:Q4_K_M", "hf.co/bartowski/X-GGUF:Q4_K_M"),
                    ("https://huggingface.co/bartowski/X-GGUF", "hf.co/bartowski/X-GGUF"),
                    ("huggingface.co/bartowski/X-GGUF/blob/main/x.gguf", "hf.co/bartowski/X-GGUF")):
    assert gy.normalize_model_name(given) == want, (given, gy.normalize_model_name(given))
for bad in ("", "rm -rf /", "hf.co/onlyuser", "a" * 250, "../../etc", "model;ls"):
    try:
        gy.normalize_model_name(bad); raise AssertionError(bad)
    except ValueError:
        pass
assert gy.normalize_model_name("https://huggingface.co/u/r-GGUF/resolve/main/r-Q8_0.gguf") == "hf.co/u/r-GGUF:Q8_0"
assert gy.normalize_model_name("hf.co/u/r/blob/main/r.IQ4_XS.gguf?download=true") == "hf.co/u/r:IQ4_XS"
assert gy.normalize_model_name("https://ollama.com/library/qwen3/tags") == "qwen3"
ok("model names: Ollama names and URLs, Hugging Face repos, URLs and single-file links; junk refused")

# Several changes from the page collapse into one; a game at that moment skips the restart
applied = []
real_apply = w.apply_settings
w.apply_settings = lambda values: applied.append(values)
for n in (8192, 16384, 32768, 65536, 131072):
    w.actions.put(("context", n))
w.actions.put(("model", "qwen3.5:9b"))
w.process_actions()
assert applied == [{"context_length": 131072, "model": "qwen3.5:9b"}], applied
w.apply_settings = real_apply
starts = ollama.get("starts", 0)
real_scan = gy.scan_games
gy.scan_games = lambda cfg: {1: "Some Game (AppId 1)"}
w.apply_settings({"context_length": 16384})
gy.scan_games = real_scan
assert ollama.get("starts", 0) == starts and gy.load_config()["context_length"] == 16384
ok("page changes collapse into one restart, and none happens while a game is starting")

# Only the changed setting is applied, not unapplied hand edits elsewhere in the file
gy.set_config_values({"cooldown_seconds": 999})                      # a hand edit, not applied yet
w.apply_settings({"context_length": 32768})
assert w.cfg["cooldown_seconds"] != 999 and w.cfg["context_length"] == 32768
gy.set_config_values({"cooldown_seconds": 45})
ok("a change from the page doesn't pick up unapplied hand edits")

# --- the page can't be broken out of by data
page = gy.render_status_page({"host": "<b>pc</b>", "notice": "</script><script>alert(1)</script>"})
assert "</script><script>alert(1)" not in page and "<title>gpu-yield on &lt;b&gt;pc&lt;/b&gt;</title>" in page
ok("status data can't inject markup into the page")
w.stop_evt.set()

# --- Wake on Wireless LAN detection
iw_info = "Wiphy phy0\n\tWoWLAN support:\n\t\t * wake up on disconnect\n\t\t * wake up on magic packet\n\tSupported commands:\n"
outputs = {("iw", "phy", "phy0", "info"): iw_info, ("iw", "phy", "phy0", "wowlan", "show"): "WoWLAN is disabled.\n"}
gy.run = lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout=outputs.get(tuple(cmd), ""), stderr="")
gy.wifi_phy = lambda dev: "phy0"
real_which = gy.shutil.which
gy.shutil.which = lambda name: "/usr/sbin/" + name
assert gy.wol_state("wlp3s0", wireless=True) == ("g", "d")
outputs[("iw", "phy", "phy0", "wowlan", "show")] = "WoWLAN is enabled:\n * wake up on magic packet\n"
assert gy.wol_state("wlp3s0", wireless=True) == ("g", "g")
outputs[("iw", "phy", "phy0", "info")] = "Wiphy phy0\n\tSupported commands:\n\t\t * magic packet\n"
assert gy.wol_state("wlp3s0", wireless=True)[0] == ""
gy.shutil.which = lambda name: None
assert gy.wol_state("wlp3s0", wireless=True) == (None, None)
gy.shutil.which = real_which
ok("Wake on Wireless LAN: reads whether the Wi-Fi card can wake the PC, and whether it's on")
print("control tests passed")
