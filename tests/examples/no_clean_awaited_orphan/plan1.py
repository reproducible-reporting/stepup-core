#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo Hello > test1.txt", inp="bogus.txt", out=["test1.txt", "test2.txt"])
