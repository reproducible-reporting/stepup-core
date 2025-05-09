#!/usr/bin/env python3
from path import Path

from stepup.core.api import amend, getinfo

# Test getinfo function
info = getinfo()
assert info.action == "runpy ./bunch.py"
assert info.workdir == "./"
assert info.inp == ["bunch.py"]
assert info.env == []
assert info.out == ["sub/", "sub/other/", "sub/other/foo/", "sub/other/foo/text"]
assert info.vol == []

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

# Test getinfo function again. It should not be affected by amendments.
info = getinfo()
assert info.action == "runpy ./bunch.py"
assert info.workdir == "./"
assert info.inp == ["bunch.py"]
assert info.env == []
assert info.out == ["sub/", "sub/other/", "sub/other/foo/", "sub/other/foo/text"]
assert info.vol == []
