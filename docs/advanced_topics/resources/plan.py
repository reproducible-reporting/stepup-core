#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("sleep 0.1; echo A", resources="cpu")
runsh("sleep 0.1; echo B", resources="gpu")
runsh("sleep 0.1; echo C", resources={"cpu": 2, "gpu": 1})
