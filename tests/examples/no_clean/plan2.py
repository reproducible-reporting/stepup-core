#!/usr/bin/env python3
from stepup.core.api import run

run("echo Hello > test2.txt", shell=True, out="test2.txt")
