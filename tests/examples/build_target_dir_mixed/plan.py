#!/usr/bin/env python3
from stepup.core.api import copy, static

static("a_input.txt", "b_input.txt", "c_input.txt", "d_input.txt")
copy("a_input.txt", "out/a.txt")
copy("b_input.txt", "solo.txt")
copy("c_input.txt", "out/optional.txt", optional=True)
copy("d_input.txt", "exact_optional.txt", optional=True)
