#!/usr/bin/env python3
from stepup.core.api import step

step("cat part2.txt", inp="part2.txt")
step("cat ../part1.txt", inp="../part1.txt")
