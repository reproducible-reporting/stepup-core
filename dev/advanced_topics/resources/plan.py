#!/usr/bin/env python3
from stepup.core.api import run

run("sleep 0.1; echo A", shell=True, resources="cpu")
run("sleep 0.1; echo B", shell=True, resources="gpu")
run("sleep 0.1; echo C", shell=True, resources={"cpu": 2, "gpu": 1})
