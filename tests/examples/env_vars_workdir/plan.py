#!/usr/bin/env python3
from stepup.core.api import run, static

static("${INP_VAR_TEST_STEPUP_WORKDIR}/input.txt")
run(
    "cat input.txt > output.txt",
    shell=True,
    inp=["input.txt"],
    out=["output.txt"],
    workdir="${INP_VAR_TEST_STEPUP_WORKDIR}",
)
