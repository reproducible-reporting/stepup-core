#!/usr/bin/env python3
from stepup.core.api import runsh

runsh('echo "spam" > first.txt', out=["first.txt"])
runsh("cp -v first.txt second.txt", inp=["first.txt"], out=["second.txt"])
