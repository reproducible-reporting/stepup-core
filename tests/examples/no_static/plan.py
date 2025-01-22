#!/usr/bin/env python3
from stepup.core.api import step

step('echo "spam" > first.txt', out=["first.txt"])
step("cp -v first.txt second.txt", inp=["first.txt"], out=["second.txt"])
