#!/usr/bin/env python3
from stepup.core.api import run, static

static("input.txt")
run(
    'echo "${STEPUP_DIRECTOR_I}" > director_i.txt',
    shell=True,
    env="STEPUP_DIRECTOR_I",
    out="director_i.txt",
)
run("tr a-z A-Z < input.txt > upper.txt", shell=True, inp="input.txt", out="upper.txt")
