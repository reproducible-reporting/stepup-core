#!/usr/bin/env python3
from stepup.core.api import run, static

static("input.txt")
run("tr a-z A-Z < input.txt > upper.txt", shell=True, inp="input.txt", out="upper.txt")
run(
    'echo "${ENV_VAR_TEST_STEPUP_PRVDGT}" > variable.txt',
    shell=True,
    env="ENV_VAR_TEST_STEPUP_PRVDGT",
    out="variable.txt",
)
run(
    'echo "${STEPUP_DIRECTOR_I}" > director_i.txt',
    shell=True,
    env="STEPUP_DIRECTOR_I",
    out="director_i.txt",
)
