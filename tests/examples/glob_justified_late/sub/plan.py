#!/usr/bin/env python3
from stepup.core.api import static

# Declares, after the fact, the same files the root plan already matched with
# glob().
static("*.txt")
