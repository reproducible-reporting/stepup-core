#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello > dir1/hello.txt", shell=True, out="dir1/hello.txt")
