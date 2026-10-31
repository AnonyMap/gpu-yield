"""The model picker used by `setup` and `gpu-yield model`."""
import builtins
import sys
import time
import types

from _util import load, ok

gy = load()
args = types.SimpleNamespace(yes=False)
shown = []


def pick(answers, ram=31.2, vram=16.0):
    it = iter(answers)
    builtins.input = lambda prompt="": next(it)
    builtins.print = lambda *a, **k: shown.append(" ".join(map(str, a)))
    sys.stdin.isatty = lambda: True
    gy.detect_memory = lambda: (ram, vram)
    try:
        return gy.choose_model(args)
    finally:
        builtins.print = real_print


real_print = builtins.print
assert pick([""]) == "gpt-oss:20b"
assert pick(["3"]) == "qwen3.5:9b"
assert pick(["9", "0", "6"]) == ""
assert pick(["5", "gemma4:12b"]) == "gemma4:12b"
ok("Enter picks the recommended model; numbers, custom names and 'none' work; bad input re-asks")

shown.clear()
pick(["1"], ram=15.5)
assert any("needs ~32 GB RAM, this PC has 16" in line for line in shown)
ok("flags the 35B model on a machine with less than 32 GB of RAM")

sys.stdin.isatty = lambda: False
assert gy.choose_model(args) is None
assert gy.choose_model(types.SimpleNamespace(yes=True)) is None
ok("never picks a model by itself (no terminal, or -y)")

calls = []


class FO(gy.Ollama):
    def start(self): calls.append("start"); return True
    def stop(self): calls.append("stop"); return True
    def active(self): return True
    def warm(self): calls.append("warm")


gy.Ollama = FO
w = gy.Watcher(dict(gy.DEFAULTS, port=1))
w.step({}, False, False, time.monotonic())
time.sleep(0.2)
assert w.state == "ai" and "warm" not in calls and "gpu-yield model" in w.warm_status
ok("with no model chosen, Ollama runs and nothing is preloaded")
print("model tests passed")
