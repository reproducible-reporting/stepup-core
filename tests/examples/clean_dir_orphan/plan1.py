#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello > dir1/hello.txt", shell=True, out="dir1/hello.txt")
run("echo world > dir2/world.txt", shell=True, out="dir2/world.txt")
