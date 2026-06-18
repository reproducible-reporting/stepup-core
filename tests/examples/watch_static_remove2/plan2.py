#!/usr/bin/env python3
from stepup.core.api import run

run("echo bye > out2.txt", shell=True, out="out2.txt")
