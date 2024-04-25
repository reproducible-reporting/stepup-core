#!/usr/bin/env python
from stepup.core.api import step

step("cat first > second", inp="first", out="second")
step("cat second > first", inp="second", out="first")
