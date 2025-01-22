#!/usr/bin/env python3
from stepup.core.api import step

step('echo "test 1" > first.txt', out=["first.txt"])
step("cp first.txt final.txt", inp=["first.txt"], out=["final.txt"])
