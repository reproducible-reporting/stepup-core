#!/usr/bin/env python3
from stepup.core.api import run, static

static("input.txt", "getstartup.py")
run('echo "${STEPUP_STARTUP_I}" > shell_i.txt', shell=True, recheck=True, out="shell_i.txt")
run("./getstartup.py", recheck=True, out="python_i.txt")
run("tr a-z A-Z < input.txt > upper.txt", shell=True, inp="input.txt", out="upper.txt")
