#!/usr/bin/env python3
from stepup.core.api import static

# A recursive ** wildcard is rejected before any globbing happens.
static("sub/**")
