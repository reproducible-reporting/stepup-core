#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("touch output.txt", out=["output.txt"])
