#!/usr/bin/env python3
from stepup.core.api import runsh

runsh('echo "test 1" > first.txt', out=["first.txt"])
runsh("cp first.txt final.txt", inp=["first.txt"], out=["final.txt"])
