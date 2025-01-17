#!/usr/bin/env python
from stepup.core.api import copy

copy("a.txt", "b.txt")
copy("b.txt", "a.txt")
