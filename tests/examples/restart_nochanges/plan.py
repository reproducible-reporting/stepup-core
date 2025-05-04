#!/usr/bin/env python3
from stepup.core.api import runsh


def duplicate(inp_path, out_path):
    runsh("cat ${inp} ${inp} > ${out}", inp=[inp_path], out=[out_path])


runsh("echo something > ${out}", out=["single.txt"])
duplicate("single.txt", "double.txt")
duplicate("double.txt", "quadruple.txt")
