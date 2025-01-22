#!/usr/bin/env python3
from stepup.core.api import step

step("echo hello > foo.txt", out="foo.txt", optional=True)
