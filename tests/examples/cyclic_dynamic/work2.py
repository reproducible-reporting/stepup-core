#!/usr/bin/env python3
from stepup.core.api import amend, run

run("echo second > inp2.txt", shell=True, out="inp2.txt")
amend(inp="inp1.txt", out="out1.txt")

with open("inp1.txt") as fr, open("out1.txt", "w") as fw:
    fw.write(fr.read())
