#!/usr/bin/env python3
from stepup.core.api import run, static

static("subdir")
run("cat subdir", inp="subdir")
