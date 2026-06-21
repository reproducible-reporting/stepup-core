#!/usr/bin/env python3
import sys

# These modules should already be in sys.modules if preloaded into the forkserver.
assert "numpy" in sys.modules, "numpy was not preloaded"
assert "matplotlib" in sys.modules, "matplotlib was not preloaded"
