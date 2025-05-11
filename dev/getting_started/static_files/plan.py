#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("limerick.txt")
runsh("nl ${inp} > ${out}", inp="limerick.txt", out="numbered.txt")
