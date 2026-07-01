#!/usr/bin/env python3
from stepup.core.api import graph, run

run(r'printf "Monday frown\nCoffee smile\n" > story.txt', shell=True, out="story.txt")
run("grep Coffee story.txt", inp="story.txt")
graph("graph")
