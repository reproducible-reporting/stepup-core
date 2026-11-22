#!/usr/bin/env python3
import os

with open("python_i.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(os.environ["STEPUP_STARTUP_I"], file=fh)
