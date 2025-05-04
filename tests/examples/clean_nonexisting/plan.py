#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo foo > bar.txt", out="bar.txt")
