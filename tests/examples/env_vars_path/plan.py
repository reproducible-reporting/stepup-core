#!/usr/bin/env python3
from stepup.core.api import static, step

static("${ENV_VAR_TEST_STEPUP_PREFIX}.txt")
# Note that `${ENV_VAR_TEST_STEPUP_PREFIX}.txt` is not in inp_paths for testing purposes.
step(
    "grep variable ${inp} ${ENV_VAR_TEST_STEPUP_PREFIX}.txt > ${out} 2> ${vol}",
    inp=["${ENV_VAR_TEST_STEPUP_PREFIX}.txt"],
    env=["ENV_VAR_TEST_STEPUP_PREFIX"],
    out=["${ENV_VAR_TEST_STEPUP_PREFIX}-stdout.txt"],
    vol=["${ENV_VAR_TEST_STEPUP_PREFIX}-stderr.txt"],
)
