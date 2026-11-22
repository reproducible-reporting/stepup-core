#!/usr/bin/env python3
from stepup.core.api import run

run('echo "${STEPUP_STARTUP_I}" > startup_i.txt', shell=True, recheck=True, out="startup_i.txt")
