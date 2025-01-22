#!/usr/bin/env python3
from stepup.core.api import copy, glob

for path_inp in glob("inp*.txt"):
    copy(path_inp, "out" + path_inp.name[3:])
