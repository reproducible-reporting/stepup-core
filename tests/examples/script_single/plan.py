#!/usr/bin/env python3
from stepup.core.api import copy, script, static

static("work/", "work/generate.py")
script("generate.py", workdir="work/", step_info="../current_step_info.json")
copy("work/test.csv", "work/copy.csv")
