#!/usr/bin/env python3
from stepup.core.api import run

run(
    "echo ${ENV_VAR_TEST_STEPUP_KQZRPX} > out.txt",
    shell=True,
    out="out.txt",
    env="ENV_VAR_TEST_STEPUP_KQZRPX",
)
