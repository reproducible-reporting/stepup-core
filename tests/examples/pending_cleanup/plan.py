#!/usr/bin/env python3
from stepup.core.api import run

run("echo data > hello.txt", shell=True, inp="missing.txt", out="hello.txt")
run("echo soon gone > bye.txt", shell=True, out="bye.txt")
