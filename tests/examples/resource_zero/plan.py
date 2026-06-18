#!/usr/bin/env python3
from stepup.core.api import run

# Both steps require a token, but STEPUP_RESOURCES=token:0 makes zero units available.
run("echo 1", resources="token")
run("echo 2", resources="token")
