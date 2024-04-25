#!/usr/bin/env python
from stepup.core.api import glob, copy, getenv

copy(getenv("ENV_VAR_TEST_STEPUP_INP"), "copy.txt")
glob("static/**", _defer=True)
