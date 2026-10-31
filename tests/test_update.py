"""Self-update from GitHub and from a file: self-test, backup, rollback, migration."""
import json
import re
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _util import SCRIPT, load, ok

gy = load()
gy.os.geteuid = lambda: 1000            # the update commands refuse to run as root
calls = []
gy.user_systemctl = lambda *a: calls.append(a) or types.SimpleNamespace(returncode=0, stdout="", stderr="")

src = SCRIPT.read_bytes()
cur = gy.VERSION


def with_version(v, data=src):
    return re.sub(rb'^VERSION = "[^"]+"', f'VERSION = "{v}"'.encode(), data, count=1, flags=re.M)


def bump(v, n):
    parts = [int(x) for x in v.split(".")]
    parts[1] += n
    return ".".join(map(str, parts))


newer, newest = bump(cur, 1), bump(cur, 2)
serve = {"file": with_version(newer)}


class GH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/repos/me/gy/contents/gpu-yield") and serve["file"] is not None:
            d = serve["file"]
        elif self.path == "/repos/ollama/ollama/releases/latest":
            d = json.dumps({"tag_name": "v0.40.10"}).encode()
        else:
            self.send_response(404); self.end_headers(); self.wfile.write(b'{"message":"Not Found"}')
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)


srv = ThreadingHTTPServer(("127.0.0.1", 0), GH)
threading.Thread(target=srv.serve_forever, daemon=True).start()
gy.GITHUB_API = f"http://127.0.0.1:{srv.server_port}"
gy.CONFIG_DIR.mkdir(parents=True)
gy.CONFIG_FILE.write_text('update_repo = "me/gy"\n')
gy.BIN_DIR.mkdir(parents=True)
gy.BIN_PATH.write_bytes(src)


def installed():
    return gy.declared_version(gy.BIN_PATH.read_bytes())


def backup():
    return gy.declared_version(gy.PREVIOUS_FILE.read_bytes()) if gy.PREVIOUS_FILE.exists() else None


def update(**kw):
    a = dict(check=False, ollama=False, force=False, file=None)
    a.update(kw)
    try:
        gy.cmd_update(types.SimpleNamespace(**a))
        return 0
    except SystemExit as e:
        return e.code


assert update(check=True) == 0 and installed() == cur
assert update() == 0 and installed() == newer and backup() == cur
assert update() == 0 and installed() == newer
ok(f"updates {cur} -> {newer}, keeps {cur} as the backup, then reports up to date")

gy.cmd_rollback(None)
assert installed() == cur and backup() == newer
gy.cmd_rollback(None)
assert installed() == newer and backup() == cur
ok("rollback, and rollback again to redo")

serve["file"] = with_version(newest, src.replace(b"def log(msg):", b"def log(msg)"))
assert update() != 0 and installed() == newer and backup() == cur
ok("a release that fails its self-test is refused and nothing changes")

serve["file"] = None
assert update() != 0 and installed() == newer
ok("a missing file on GitHub is a clear error")

serve["file"] = with_version(newer)
assert update(force=True) == 0 and installed() == newer and backup() == cur
ok("reinstalling the same version keeps the real previous version as the backup")

old = with_version("1.0.0", re.sub(rb'if sys.argv\[1:2\] == \["_post-update"\]:', b"if False:", src))
f = gy.HOME / "old-gpu-yield"
f.write_bytes(old)
calls.clear()
assert update(file=str(f)) == 0 and installed() == "1.0.0"
assert ("restart", gy.WATCHER_UNIT) in calls
ok("installing a version without the _post-update step still restarts the watcher")
print("update tests passed")
