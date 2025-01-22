#!/usr/bin/env python3
from stepup.core.api import step

step("cat first > second", inp="first", out="second")
step("cat second > first", inp="second", out="first")
