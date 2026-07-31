#!/usr/bin/env python3
from stepup.core.api import copy, glob, static

# glob() is now a pure query: it declares nothing, so static(ng) is the sole
# declaration of its matches. It also must not register the pattern a second time.
ng = glob("inp*.txt")
static(ng)
for path in ng:
    copy(path, "out" + path.name[3:])
