#!/usr/bin/env python3
from stepup.core.api import copy, static

static("index.md")
copy("index.md", "${ROOT}/${PUBLIC}/${HERE}/")
