#!/usr/bin/env python3
from stepup.core.api import glob

# NamedGlob does not skip dot entries, so "*" in the project root would match
# ".stepup/" if register_glob did not explicitly reject it.
list(glob("*"))
