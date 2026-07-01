#!/usr/bin/env python3
from stepup.core.api import graph, run

run("echo Monday frown > story.txt; echo Coffee smile >> story.txt", shell=True, out="story.txt")
run("grep Coffee story.txt", inp="story.txt")
graph("graph")
