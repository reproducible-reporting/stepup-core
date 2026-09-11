#!/usr/bin/env python3
import sys

# These modules should already be in sys.modules if preloaded into the forkserver.
assert "wave" in sys.modules, "wave was not preloaded"
assert "xml.dom.minidom" in sys.modules, "xml.dom.minidom was not preloaded"
