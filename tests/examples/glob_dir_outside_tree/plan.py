#!/usr/bin/env python3
from stepup.core.api import glob

# No static tree declared under sub/. A directory match no longer has to lie inside
# a static tree, so this succeeds: glob() is a pure query and does not require the
# match to be justified at registration time. A later phase adds a check that rejects
# a directory match that never becomes justified some other way.
list(glob("sub/*/"))
