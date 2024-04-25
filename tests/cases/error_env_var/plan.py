#!/usr/bin/env python
from stepup.core.api import step

step("echo > foo", out=["${ENV_VAR_TEST_STEPUP_NON_EXISTING}.txt"])
