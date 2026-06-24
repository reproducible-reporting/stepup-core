#!/usr/bin/env python3
from stepup.core.api import copy, getenv, static

copy(getenv("ENV_VAR_TEST_STEPUP_INP"), "copy.txt")
static("static")
