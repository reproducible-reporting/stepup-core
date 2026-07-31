#!/usr/bin/env python3
from stepup.core.api import copy, run

run("false", out=["broken.txt"])
copy("broken.txt", "final.txt")
