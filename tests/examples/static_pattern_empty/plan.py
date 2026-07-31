#!/usr/bin/env python3
from stepup.core.api import static

# Zero matches is not an error: the pattern is registered anyway, so a later run can
# react to a match that appears afterwards.
paths = static("nothing*.txt")
assert paths == []
