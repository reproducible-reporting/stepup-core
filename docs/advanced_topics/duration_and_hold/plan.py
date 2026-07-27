#!/usr/bin/env python3
from stepup.core.api import hold, run

with hold():
    run("sleep 2.0", duration=2.0)
    run("sleep 2.1", duration=2.1)
    run("sleep 4.0", duration=4.0)
    run("sleep 4.1", duration=4.1)
