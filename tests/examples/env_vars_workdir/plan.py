#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("${INP_VAR_TEST_STEPUP_WORKDIR}/")
static("${INP_VAR_TEST_STEPUP_WORKDIR}/input.txt")
runsh(
    "cat input.txt > output.txt",
    inp=["input.txt"],
    out=["output.txt"],
    workdir="${INP_VAR_TEST_STEPUP_WORKDIR}",
)
