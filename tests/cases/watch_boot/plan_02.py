#!/usr/bin/env python
from stepup.core.api import step

step('echo "test 2" > second.txt', out=["second.txt"])
step("cp second.txt final.txt", inp=["second.txt"], out=["final.txt"])
