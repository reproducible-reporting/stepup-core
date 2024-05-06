#!/usr/bin/env python
from path import Path

from stepup.core.api import amend

# Make files as anticipated
nested1 = Path("sub/other/foo/")
nested1.makedirs_p()
with open(nested1 / "text", "w") as fh:
    print("Some fo text", file=fh)

# Make some more amended stuff
amend(out=["sub/one/", "sub/one/bar/", "sub/one/bar/text"])
nested2 = Path("sub/one/bar/")
nested2.makedirs_p()
with open(nested2 / "text", "w") as fh:
    print("Some bar text", file=fh)
