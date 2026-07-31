#!/usr/bin/env python3
from stepup.core.api import copy, static

# The captures of a named wildcard are not exposed by static(); only the return value
# (a flat list of paths) is used here.
for path in static("sub/${*name}.txt"):
    copy(path, "out_" + path.name)
