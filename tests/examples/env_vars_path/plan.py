#!/usr/bin/env python3
from stepup.core.api import run, shq, static

static("${ENV_VAR_TEST_STEPUP_PREFIX}.txt")
# Note that `${ENV_VAR_TEST_STEPUP_PREFIX}.txt` is not in inp_paths for testing purposes.
inp_paths = ["${ENV_VAR_TEST_STEPUP_PREFIX}.txt"]
out_paths = ["${ENV_VAR_TEST_STEPUP_PREFIX}-stdout.txt"]
vol_paths = ["${ENV_VAR_TEST_STEPUP_PREFIX}-stderr.txt"]
run(
    f"grep variable {shq(inp_paths)} "
    "${ENV_VAR_TEST_STEPUP_PREFIX}.txt"
    f" > {shq(out_paths)} 2> {shq(vol_paths)}",
    shell=True,
    inp=inp_paths,
    env=["ENV_VAR_TEST_STEPUP_PREFIX"],
    out=out_paths,
    vol=vol_paths,
)
