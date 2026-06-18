#!/usr/bin/env python3
from stepup.core.api import run

run("echo foo > bar.txt", shell=True, out="bar.txt")
