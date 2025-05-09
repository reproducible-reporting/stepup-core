#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("cat part2.txt", inp="part2.txt")
runsh("cat ../part1.txt", inp="../part1.txt")
