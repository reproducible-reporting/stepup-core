#!/usr/bin/env python3
from stepup.core.api import run


def duplicate(inp_path, out_path):
    run("cat ${inp} ${inp} > ${out}", shell=True, inp=[inp_path], out=[out_path])


run("echo something > ${out}", shell=True, out=["single.txt"])
duplicate("single.txt", "double.txt")
duplicate("double.txt", "quadruple.txt")
