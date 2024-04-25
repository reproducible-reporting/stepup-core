#!/usr/bin/env python
from stepup.core.api import static, step

static("${INP_VAR_TEST_STEPUP_WORKDIR}/")
static("${INP_VAR_TEST_STEPUP_WORKDIR}/input.txt")
step(
    "cat input.txt > output.txt",
    inp=["${INP_VAR_TEST_STEPUP_WORKDIR}/input.txt"],
    out=["${INP_VAR_TEST_STEPUP_WORKDIR}/output.txt"],
    workdir="${INP_VAR_TEST_STEPUP_WORKDIR}/",
)
