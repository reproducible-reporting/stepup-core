#!/usr/bin/env python3
from stepup.core.api import runsh

runsh('echo "test 2" > second.txt', out=["second.txt"])
runsh("cp second.txt final.txt", inp=["second.txt"], out=["final.txt"])
