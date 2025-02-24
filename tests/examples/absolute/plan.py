#!/usr/bin/env python3
from stepup.core.api import copy, getenv, static
from stepup.core.utils import myparent

tmp = getenv("mytmpdir", path=True)
path_src = tmp / "this_is_potentially_unsafe_18731"
path_dst = tmp / "this_is_potentially_unsafe_79824"
paths = [tmp / ""]
while True:
    parent = myparent(paths[-1])
    if parent == "/":
        break
    paths.append(parent)
static(*paths, path_src)
copy(path_src, path_dst)
