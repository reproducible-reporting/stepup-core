#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello > out.txt", shell=True, out=["out.txt"])
