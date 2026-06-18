#!/usr/bin/env python3
from stepup.core.api import run

run('echo "test 2" > second.txt', shell=True, out=["second.txt"])
run("cp second.txt final.txt", inp=["second.txt"], out=["final.txt"])
