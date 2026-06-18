#!/usr/bin/env python3
from stepup.core.api import run

run("echo blub blub > vol.txt", shell=True, vol="vol.txt")
