#!/usr/bin/env python
from stepup.core.api import step


def duplicate(inp_path, out_path):
    step("cat ${inp} ${inp} > ${out}", inp=[inp_path], out=[out_path])


step("echo something > ${out}", out=["single.txt"])
duplicate("single.txt", "double.txt")
duplicate("double.txt", "quadruple.txt")
