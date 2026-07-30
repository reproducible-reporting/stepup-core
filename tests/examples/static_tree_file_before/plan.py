#!/usr/bin/env python3
from stepup.core.api import static

static("src/foo.txt")
# A static tree must be declared before any file it contains: this raises GraphError.
static("src/")
