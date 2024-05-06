#!/usr/bin/env python
from stepup.core.api import copy, getenv, glob

for fn_raw in glob("raw_*.txt"):
    copy(fn_raw, "converted_" + fn_raw[4:], optional=True)

idx = int(getenv("ENV_VAR_TEST_STEPUP_IDX"))
copy(f"converted_{idx:02d}.txt", "used.txt")
