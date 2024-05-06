#!/usr/bin/env python
import os

with open("current_variables.txt", "w") as fh:
    for name, value in os.environ.items():
        if name.startswith("ENV_VAR_TEST_STEPUP_"):
            print(f"{name}={value!r}", file=fh)
