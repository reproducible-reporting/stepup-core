#!/usr/bin/env python3
from stepup.core.api import pool, step

pool("random", 1)
step("echo foo > bar.txt", out="bar.txt", pool="random")
