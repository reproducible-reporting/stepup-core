#!/usr/bin/env python3
from stepup.core.api import amend, runsh

runsh("echo first > inp1.txt", out="inp1.txt")
amend(inp="inp2.txt", out="out2.txt")

with open("inp2.txt") as fr, open("out2.txt", "w") as fw:
    fw.write(fr.read())
