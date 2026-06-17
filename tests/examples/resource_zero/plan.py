#!/usr/bin/env python3
from stepup.core.api import runsh

# Both steps require a token, but STEPUP_RESOURCES=token:0 makes zero units available.
runsh("echo 1", resources="token")
runsh("echo 2", resources="token")
