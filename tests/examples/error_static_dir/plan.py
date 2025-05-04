#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("subdir")
runsh("cat subdir", inp="subdir")
