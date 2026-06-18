#!/usr/bin/env python3
from stepup.core.api import run, static

static("limerick.txt")
run("nl ${inp} > ${out}", shell=True, inp="limerick.txt", out="numbered.txt")
