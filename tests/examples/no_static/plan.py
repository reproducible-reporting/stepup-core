#!/usr/bin/env python3
from stepup.core.api import run

run('echo "spam" > first.txt', shell=True, out=["first.txt"])
run("cp -v first.txt second.txt", inp=["first.txt"], out=["second.txt"])
