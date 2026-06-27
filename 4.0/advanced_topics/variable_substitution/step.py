#!/usr/bin/env python3
import sys

from stepup.core.api import getenv

myname = repr(getenv("MYNUM"))
print(f"MYNUM={myname}\n")
print("Read from stdin:")
print(sys.stdin.read())
