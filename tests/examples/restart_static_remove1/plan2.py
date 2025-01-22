#!/usr/bin/env python3
from stepup.core.api import copy, static

static("README.md")
copy("README.md", "backup.md")
