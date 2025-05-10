#!/usr/bin/env python3
from stepup.core.api import call, getenv, static

static("single.txt")
root = getenv("ROOT", path=True)
repeat = call(root / "scripts/repeat.py", inp=[None, "single.txt"], out=[False, "multi.txt"], n=5)

EXPECTED_COMMAND = """
runpy ../scripts/repeat.py
'{"n": 5, "inp": ["single.txt"], "out": ["multi.txt"]}'
""".replace("\n", " ").strip()
if repeat.action != EXPECTED_COMMAND:
    print(repeat.action)
    raise AssertionError("Unexpected command")
