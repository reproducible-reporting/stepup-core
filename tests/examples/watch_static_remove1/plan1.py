#!/usr/bin/env python3
from stepup.core.api import copy, static

static("README.md", "foo.txt")
copy("foo.txt", "bar.txt")
copy("README.md", "backup.md")
