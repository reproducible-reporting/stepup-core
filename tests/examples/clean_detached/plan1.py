#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello1 > hello1.txt", shell=True, out="hello1.txt")
