#!/usr/bin/env python
from stepup.core.api import glob

glob("static/**", _defer=True)
