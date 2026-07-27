#!/usr/bin/env python3
from stepup.core.api import run

run("false")
run("touch independent.txt", out=["independent.txt"])
