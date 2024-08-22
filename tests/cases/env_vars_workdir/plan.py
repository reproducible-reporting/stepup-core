#!/usr/bin/env python
from stepup.core.api import static, step

static("${INP_VAR_TEST_STEPUP_WORKDIR}/")
static("${INP_VAR_TEST_STEPUP_WORKDIR}/input.txt")
step(
    "cat input.txt > output.txt",
    inp=["input.txt"],
    out=["output.txt"],
    workdir="${INP_VAR_TEST_STEPUP_WORKDIR}",
)
