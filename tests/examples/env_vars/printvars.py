#!/usr/bin/env python3
import os

with open("current_variables.txt", "w", encoding="utf-8", newline="\n") as fh:
    for name, value in sorted(os.environ.items()):
        if name.startswith("ENV_VAR_TEST_STEPUP_"):
            print(f"{name}={value!r}", file=fh)
