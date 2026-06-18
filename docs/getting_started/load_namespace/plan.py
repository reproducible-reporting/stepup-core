#!/usr/bin/env python3
from stepup.core.api import run, static

static("config.toml", "print_sentence.py")
run("./print_sentence.py")
