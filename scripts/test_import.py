#!/usr/bin/env python3
"""Minimal test to see if we can import and run."""
import sys
import os

os.environ['PYTHONUNBUFFERED'] = '1'

print("TEST SCRIPT START", file=sys.stderr, flush=True)
print("TEST SCRIPT START", flush=True)

# Test if we can even import Path
from pathlib import Path  # noqa: E402
print("✓ Path imported", flush=True)

# Test if we can import from scripts
sys.path.insert(0, str(Path(__file__).parent.parent))
print("✓ Added to path", flush=True)

print("About to import run_re_bench...", flush=True)
import scripts.run_re_bench  # noqa: E402
print("✓ Imported run_re_bench module", flush=True)

print("About to get run_pair_programming function...", flush=True)
func = scripts.run_re_bench.run_pair_programming  # noqa: E402
print("✓ Got function", flush=True)

print("SUCCESS - all imports worked!", flush=True)


