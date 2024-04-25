#!/usr/bin/env python
from stepup.core.api import step

step("echo First line. > ${out}; echo Second line. >> ${out}", out="story.txt")
step("grep First ${inp}", inp="story.txt")
