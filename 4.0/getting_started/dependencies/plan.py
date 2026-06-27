#!/usr/bin/env python3
from stepup.core.api import graph, run

run("echo Monday frown > ${out}; echo Coffee smile >> ${out}", shell=True, out="story.txt")
run("grep Coffee ${inp}", inp="story.txt")
graph("graph")
