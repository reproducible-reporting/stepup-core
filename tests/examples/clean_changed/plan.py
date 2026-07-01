#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello > hello.txt", shell=True, out="hello.txt")
