#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("config.toml")
runsh("./print_sentence.py")
