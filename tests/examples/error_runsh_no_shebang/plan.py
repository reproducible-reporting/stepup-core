#!/usr/bin/env python3
from stepup.core.api import run, static

static("simple.sh")
run("./simple.sh", inp="simple.sh")
