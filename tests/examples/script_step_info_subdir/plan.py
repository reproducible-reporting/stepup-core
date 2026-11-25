#!/usr/bin/env python3
from stepup.core.api import script, static

static("work/generate.py", "work/config.json")
script("generate.py", workdir="work/", step_info="../data/step_info.json")
