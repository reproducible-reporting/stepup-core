#!/usr/bin/env python
from stepup.core.api import step

step("cp -v first.txt second.txt", inp="first.txt", out="second.txt", vol="third.txt")
