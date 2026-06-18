#!/usr/bin/env python3
from stepup.core.api import run

run("touch input.txt", out=["input.txt"])
run("cp input.txt wrong.txt", inp=["input.txt"], out=["output.txt"])
