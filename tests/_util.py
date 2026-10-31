"""Shared helpers: load the gpu-yield script as a module inside a throwaway home folder."""
import importlib.machinery
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True          # keep __pycache__ out of the repo
SCRIPT = Path(__file__).resolve().parents[1] / "gpu-yield"


def load(home=None):
    home = Path(home or tempfile.mkdtemp(prefix="gy-home-"))
    os.environ["HOME"] = str(home)
    for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        os.environ.pop(k, None)
    (home / "run").mkdir(exist_ok=True)
    os.environ["XDG_RUNTIME_DIR"] = str(home / "run")
    loader = importlib.machinery.SourceFileLoader("gpu_yield", str(SCRIPT))
    spec = importlib.util.spec_from_loader("gpu_yield", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(cond, timeout=10, msg=""):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for: " + msg)


def fake_process(argv0, *args):
    """A sleeping process whose /proc cmdline starts with argv0 (like Steam's reaper).
    One process (no child to outlive kill()), with no pipes to hold the test runner open."""
    script = "import time; time.sleep(120)"
    return subprocess.Popen([argv0, "-c", script, *args], executable=sys.executable,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ok(label):
    print(f"  ok  {label}", flush=True)
