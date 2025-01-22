#!/usr/bin/env python3
from stepup.core.api import step

step("echo hello > ${out}", out="hello.txt")
