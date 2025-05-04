#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("touch done.txt", out="done.txt")
