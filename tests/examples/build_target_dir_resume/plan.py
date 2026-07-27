#!/usr/bin/env python3
from stepup.core.api import copy, static

static("a_input.txt", "b_input.txt")
copy("a_input.txt", "out/a.txt")
copy("b_input.txt", "out/b.txt")
copy("a_input.txt", "other.txt")
