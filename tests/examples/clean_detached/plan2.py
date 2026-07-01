#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello2 > hello2.txt", shell=True, out="hello2.txt")
