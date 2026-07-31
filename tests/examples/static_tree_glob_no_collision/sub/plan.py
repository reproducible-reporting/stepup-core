#!/usr/bin/env python3
from stepup.core.api import copy, glob

# ../plan.py's step owns ../data/ as a static tree. glob() is a pure query after Phase 2:
# it declares nothing, so a pattern matching inside someone else's tree is not a
# collision, unlike static() naming the same path would be.
ng = glob("../data/*.txt")
for path in ng:
    copy(path, path.name)
