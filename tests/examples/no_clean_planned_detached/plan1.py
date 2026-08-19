#!/usr/bin/env python3
from stepup.core.api import run

run("echo Hello > test1.txt", shell=True, inp="bogus.txt", out=["test1.txt", "test2.txt"])
