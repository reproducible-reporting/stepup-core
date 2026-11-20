#!/usr/bin/env python3
from stepup.core.api import run, static

static("getepoch.py")
# This step declares the variable up front.
run(
    'echo "${SOURCE_DATE_EPOCH-unset}" > epoch_env.txt',
    shell=True,
    env="SOURCE_DATE_EPOCH",
    out="epoch_env.txt",
)
# This step discovers the variable while it runs.
run("./getepoch.py", inp="getepoch.py", out="epoch_getenv.txt")
