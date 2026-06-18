#!/usr/bin/env python3
from stepup.core.api import run

run('echo "test 1" > first.txt', shell=True, out=["first.txt"])
run("cp first.txt final.txt", inp=["first.txt"], out=["final.txt"])
