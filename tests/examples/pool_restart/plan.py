#!/usr/bin/env python3
from stepup.core.api import pool, runsh

pool("random", 1)
runsh("echo foo > bar.txt", out="bar.txt", pool="random")
