#!/usr/bin/env python
from stepup.core.api import step

step('echo "spam" > first.txt', out=["first.txt"])
step("cp -v first.txt second.txt", inp=["first.txt"], out=["second.txt"])
