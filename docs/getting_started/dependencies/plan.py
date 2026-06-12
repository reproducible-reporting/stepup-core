#!/usr/bin/env python3
from stepup.core.api import graph, runsh

runsh("echo Monday frown > ${out}; echo Coffee smile >> ${out}", out="story.txt")
runsh("grep Coffee ${inp}", inp="story.txt")
graph("graph")
