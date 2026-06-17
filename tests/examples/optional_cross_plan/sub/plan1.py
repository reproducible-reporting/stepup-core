#!/usr/bin/env python3
from stepup.core.api import copy

# Consumes ../out.txt, so the optional producer becomes needed (implied DEFAULT).
copy("../out.txt", "final.txt")
