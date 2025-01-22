#!/usr/bin/env python3
from stepup.core.api import copy, static

path_src = "/tmp/this_is_potentially_unsafe_18731"
path_dst = "/tmp/this_is_potentially_unsafe_79824"
static("/tmp/", path_src)
copy(path_src, path_dst)
