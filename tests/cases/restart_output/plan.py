#!/usr/bin/env python
from stepup.core.api import static, copy

static("original.txt")
copy("original.txt", "copy.txt")
