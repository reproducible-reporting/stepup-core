#!/usr/bin/env python3
from stepup.core.api import run

run("cat first > second", shell=True, inp="first", out="second")
run("cat second > first", shell=True, inp="second", out="first")
