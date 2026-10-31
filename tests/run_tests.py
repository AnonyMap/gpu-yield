#!/usr/bin/env python3
"""Run every tests/test_*.py in its own process. Usage: python3 tests/run_tests.py"""
import subprocess
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
failed = []
for test in sorted(here.glob("test_*.py")):
    t = time.time()
    r = subprocess.run([sys.executable, "-B", str(test)], capture_output=True, text=True, timeout=300)
    status = "PASS" if r.returncode == 0 else "FAIL"
    print(f"{status}  {test.name}  ({time.time() - t:.1f}s)")
    if r.returncode:
        failed.append(test.name)
        print("\n".join("      " + line for line in (r.stdout + r.stderr).strip().splitlines()[-25:]))
print(f"\n{len(failed)} failed" if failed else "\nall passed")
sys.exit(1 if failed else 0)
