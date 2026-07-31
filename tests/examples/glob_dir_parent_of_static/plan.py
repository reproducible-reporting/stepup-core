#!/usr/bin/env python3
from stepup.core.api import glob, static

# sub/foo.txt is declared directly, without a static tree.
static("sub/foo.txt")

# The directory match "sub/" is justified because it is the parent of a static file,
# without needing a static tree of its own. The pattern is scoped to "s*/" rather than
# "*/", since NamedGlob does not skip dot entries and "*/" would also match ".stepup/".
print("DIRS:", sorted(str(p) for p in glob("s*/")))
