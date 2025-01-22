#!/usr/bin/env python3
from stepup.core.api import step

step("echo foo > bar.txt", out="bar.txt")
