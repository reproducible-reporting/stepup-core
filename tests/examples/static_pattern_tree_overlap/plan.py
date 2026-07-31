#!/usr/bin/env python3
from stepup.core.api import static

# Three overlapping declarations of the same directory tree, in one plan.
# The same-creator no-op is what keeps this from raising.
static("data/")
static("data/*/")
static("data/*")
