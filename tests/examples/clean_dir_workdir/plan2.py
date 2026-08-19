#!/usr/bin/env python3
from stepup.core.api import run

run("echo keep > keep.txt", shell=True, out="keep.txt")
