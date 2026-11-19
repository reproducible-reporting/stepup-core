#!/usr/bin/env python3
from pathlib import Path

from stepup.core.api import amend

amend(inp="data/sub/a.txt" if Path("data/sub/a.txt").exists() else "data/b.txt")
