"""Watcher state machine, stand-in and health checks, against a fake Ollama server."""
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _util import fake_process, free_port, load, ok, wait

gy = load()
PORT = free_port()
events = []
loaded = {"models": []}          # what the fake /api/ps reports
alive = {"answer": True}         # False = process up but API hung


class FakeOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body):
        d = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_GET(self):
        if not alive["answer"]:
            time.sleep(3)        # longer than the client timeout
            return
        self._send({"version": "fake"} if self.path == "/api/version" else loaded)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        events.append(("generate", req))
        if req["model"] == "missing:model":
            d = b'{"error":"model not found"}'
            self.send_response(404)
            self.send_header("Content-Length", str(len(d)))
            self.end_headers()
            self.wfile.write(d)
            return
        time.sleep(0.2)
        loaded["models"] = [{"name": req["model"], "size": 100, "size_vram": 100,
                             "context_length": req["options"]["num_ctx"]}]
        self._send({"done": True})


class FakeOllama(gy.Ollama):
    srv = None

    def active(self):
        return FakeOllama.srv is not None

    def start(self):
        events.append(("start",))
        if not FakeOllama.srv:
            FakeOllama.srv = ThreadingHTTPServer(("127.0.0.1", PORT), FakeOllamaHandler)
            threading.Thread(target=FakeOllama.srv.serve_forever, daemon=True).start()
        return True

    def stop(self):
        events.append(("stop",))
        if FakeOllama.srv:
            FakeOllama.srv.shutdown()
            FakeOllama.srv.server_close()
            FakeOllama.srv = None
        loaded["models"] = []
        return True


gy.Ollama = FakeOllama
gy.ensure_ollama_unit = lambda cfg: False

cfg = dict(gy.load_config(), port=PORT, poll_seconds=0.3, cooldown_seconds=2,
           model="gpt-oss:20b", context_length=65536)
w = gy.Watcher(cfg)
threading.Thread(target=w.run, daemon=True).start()


def get(path, data=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
    try:
        c.request("POST" if data else "GET", path, body=data)
        r = c.getresponse()
        return r.status, r.read().decode(), dict(r.getheaders())
    except OSError as e:
        return None, repr(e), {}
    finally:
        c.close()


def starts():
    return len([e for e in events if e[0] == "start"])


wait(lambda: w.state == "ai" and w.warm_status.startswith("ready"), msg="initial load")
gen = [e for e in events if e[0] == "generate"][-1][1]
assert gen["keep_alive"] == -1 and gen["options"]["num_ctx"] == 65536 and gen["prompt"] == ""
ok("starts Ollama and preloads the model with the configured context")

game = fake_process("reaper", "SteamLaunch", "AppId=1245620", "--", "game")
t0 = time.time()
wait(lambda: w.state == "gaming" and w.standin.server is not None, msg="gaming")
ok(f"game detected and Ollama stopped in {time.time() - t0:.1f}s")
code, body, hdr = get("/v1/chat/completions", b'{"model":"x","messages":[]}')
assert code == 503 and json.loads(body)["error"]["code"] == "gpu_busy", (code, body)
assert hdr.get("Connection") == "close" and hdr.get("Retry-After") == "120"
code, body, _ = get("/api/chat", b"{}")
assert code == 503 and "game is running" in json.loads(body)["error"]
ok("stand-in answers 503 in OpenAI and Ollama formats, closing each connection")

# A client holding a keep-alive connection must reach the real Ollama once it's back.
held = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
held.request("GET", "/api/version")
r = held.getresponse(); r.read()
assert r.status == 503

game.kill(); game.wait()
wait(lambda: w.state == "cooldown", msg="cooldown")
n = starts()
game = fake_process("reaper", "SteamLaunch", "AppId=99", "--", "game")
wait(lambda: w.state == "gaming", msg="regame")
assert starts() == n
ok("a game starting during cooldown goes straight back to gaming, no reload")
game.kill(); game.wait()
wait(lambda: w.state == "ai" and w.warm_status.startswith("ready"), msg="resume")
held.request("GET", "/api/version")
r = held.getresponse(); r.read()
assert r.status == 200, "client stuck on the stand-in after Ollama came back"
held.close()
ok("resumes after cooldown; a previously open client connection reaches the real Ollama")

st = json.loads(gy.STATUS_FILE.read_text())
assert st["state"] == "ai" and str(gy.STATUS_FILE).startswith(gy.os.environ["XDG_RUNTIME_DIR"])
ok("status file lives in the runtime dir (tmpfs), not on disk")

gy.PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
gy.PAUSE_FILE.touch()
wait(lambda: w.state == "paused" and w.standin.server is not None, msg="pause")
assert get("/api/tags")[0] == 503
gy.PAUSE_FILE.unlink()
wait(lambda: w.state == "ai" and w.warm_status.startswith("ready"), msg="unpause")
ok("pause and resume")

w.step({}, False, True, time.monotonic())
wait(lambda: w.warm_status.startswith("ready"), msg="after suspend")
assert [e[0] for e in events[-3:]] == ["stop", "start", "generate"]
ok("restarts Ollama after waking from sleep")

# --- health checks (call directly instead of waiting 15 s each)
def check():
    w.last_health = 0
    w.health(time.monotonic() + 1000)


n_gen = len([e for e in events if e[0] == "generate"])
loaded["models"] = []                     # Ollama restarted by itself / model dropped
check(); check()
wait(lambda: len([e for e in events if e[0] == "generate"]) > n_gen and w.warm_status.startswith("ready"),
     msg="re-warm")
ok("reloads the model when Ollama no longer has it loaded")

loaded["models"] = [{"name": "llama3.1:8b", "size": 1, "size_vram": 1}]
n_gen = len([e for e in events if e[0] == "generate"])
check(); check()
assert "llama3.1:8b" in w.notice and len([e for e in events if e[0] == "generate"]) == n_gen
ok("warns (without fighting it) when a client loads a different model")

loaded["models"] = [{"name": "gpt-oss:20b", "size": 1, "size_vram": 1, "context_length": 131072}]
check()
assert "131072" in w.notice and "65536" in w.notice
loaded["models"][0]["context_length"] = 65536
check()
assert w.notice == ""
ok("warns when a client reloads the model with a different context, clears when fixed")

n = starts()
alive["answer"] = False
w.last_restart = -1000
for _ in range(3):
    check()
alive["answer"] = True
wait(lambda: starts() > n and w.warm_status.startswith("ready"), msg="hang restart")
ok("restarts Ollama when it stops answering")

w.cfg = w.ollama.cfg = dict(w.cfg, model="missing:model")
w.start_warm()
wait(lambda: w.warm_status.startswith("failed"), msg="404")
assert w.warm_retry == 30 and "gpu-yield model missing:model" in w.warm_status
ok("a model that isn't downloaded is retried every 30 s with a useful hint")

w.stop_evt.set()
print("watcher tests passed")
