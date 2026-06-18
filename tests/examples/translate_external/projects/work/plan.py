#!/usr/bin/env python3
from stepup.core.api import run, static

static("../../common/script.py")
run("./script.py", workdir="../../common")
