#!/usr/bin/env python3
from stepup.core.api import copy, static

static("README.txt", "foo.txt")
copy("foo.txt", "bar.txt")
copy("README.txt", "backup.txt")
