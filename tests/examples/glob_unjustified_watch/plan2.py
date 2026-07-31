#!/usr/bin/env python3
from stepup.core.api import static

# The missing declaration is added: the warning disappears once this plan runs.
static("data/*.txt")
