#!/usr/bin/env python3
from stepup.core.api import run

run("echo test1 > test1.txt", shell=True, out="test1.txt", optional=True)
run("echo test2 > test2.txt", shell=True, out="test2.txt", optional=True)
run("cat test2.txt", inp="test2.txt")
