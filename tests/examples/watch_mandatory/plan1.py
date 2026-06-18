#!/usr/bin/env python3
from stepup.core.api import run

run("echo hello > foo.txt", shell=True, out="foo.txt", optional=True)
