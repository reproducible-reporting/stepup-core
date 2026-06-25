#!/usr/bin/env python3
from stepup.core.api import copy, getenv, glob

for fn_raw in glob("raw*.txt"):
    copy(fn_raw, "converted" + fn_raw[3:], optional=True)

idx = int(getenv("ENV_VAR_TEST_STEPUP_IDX", "123456"))
copy(f"converted{idx:01d}.txt", "used.txt")
