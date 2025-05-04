#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo bye > out2.txt", out="out2.txt")
