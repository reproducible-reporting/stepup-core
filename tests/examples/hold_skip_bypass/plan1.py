#!/usr/bin/env python3
"""Build the "cached" step once, outside any hold(), so it has a stored hash.

The second phase (`plan2.py`) redeclares this exact step (same command, inputs and
outputs) inside a `hold()` block, where it must be recycled and skipped, next to a
brand-new sibling that has no stored hash yet and must actually run.
"""

from stepup.core.api import static, step

static("work.py")
step("./work.py cached cached.txt", inp="work.py", out="cached.txt")
