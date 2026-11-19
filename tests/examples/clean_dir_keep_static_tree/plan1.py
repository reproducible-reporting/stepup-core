#!/usr/bin/env python3
from stepup.core.api import run

run("echo x > data/x.txt", shell=True, out="data/x.txt")
