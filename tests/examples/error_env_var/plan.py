#!/usr/bin/env python3
from stepup.core.api import run

run("echo > foo", shell=True, out=["${ENV_VAR_TEST_STEPUP_NON_EXISTING}.txt"])
