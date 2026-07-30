#!/usr/bin/env python3
from stepup.core.api import copy, static

static(".gitignore")
copy(".gitignore", ".stepup/")
