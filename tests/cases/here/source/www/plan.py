#!/usr/bin/env python
from stepup.core.api import copy, static

static("index.md")
copy("index.md", "${ROOT}/${PUBLIC}/${HERE}/")
