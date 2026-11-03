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
assert pick([""]) == "qwen3.5:9b"
assert pick(["3"]) == "qwen3.5:4b"
assert pick(["9", "0", "5"]) == ""
assert pick(["4", "gemma4:12b"]) == "gemma4:12b"
ok("Enter picks the recommended model; numbers, custom names and 'none' work; bad input re-asks")

assert all(name not in {"gpt-oss:20b", "qwen3.6:35b-a3b"} for name, *_ in gy.MODEL_CHOICES)
shown.clear()
pick(["1"], vram=11.98)                          # an RX 6700 XT
assert any("gemma4" in line and "needs a 16 GB card" in line for line in shown)
assert not any("qwen3.5:9b" in line and "needs" in line for line in shown)
shown.clear()
pick(["1"], vram=15.98)                          # an RX 7800 XT reports a bit under 16
assert not any("needs a" in line for line in shown)
ok("offers only models that fit at 64K context, and flags one too big for this card")

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
