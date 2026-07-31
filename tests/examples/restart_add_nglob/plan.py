#!/usr/bin/env python3
from stepup.core.api import copy, static

for path_inp in static("inp*.txt"):
    copy(path_inp, "out" + path_inp.name[3:])
