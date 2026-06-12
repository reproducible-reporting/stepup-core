#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("config.toml", "print_sentence.py")
runpy("./print_sentence.py")
