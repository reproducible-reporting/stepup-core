#!/usr/bin/env python3
from stepup.core.api import static

# Two overlapping patterns from one plan both match data/sub/a.txt.
# The same-creator no-op is what lets this compose instead of raising.
static("data/*/*.txt")
static("data/sub/*.*")
