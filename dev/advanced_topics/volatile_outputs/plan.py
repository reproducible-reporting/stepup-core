#!/usr/bin/env python3
from stepup.core.api import run

run("date > date.txt", shell=True, vol="date.txt")
