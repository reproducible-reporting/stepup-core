#!/usr/bin/env python3
from stepup.core.api import static

# A ** wildcard is only rejected as the final path component.
# Here it appears in the middle, so the pattern is expanded eagerly, like any other.
paths = static("sub/**/*.txt")
assert paths == ["sub/a.txt", "sub/deeper/b.txt"]
