#!/usr/bin/env python3
from stepup.core.api import getenv

digest = getenv("STEPUP_STEP_INP_DIGEST")
assert digest is not None
