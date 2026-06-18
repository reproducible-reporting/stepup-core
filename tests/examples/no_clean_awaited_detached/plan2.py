#!/usr/bin/env python3
from stepup.core.api import run

run("echo Hello > test1.txt", shell=True, out="test1.txt")
