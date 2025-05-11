#!/usr/bin/env python3
from stepup.core.api import graph, runsh

runsh("echo First line. > ${out}; echo Second line. >> ${out}", out="story.txt")
runsh("grep First ${inp}", inp="story.txt")
graph("graph")
