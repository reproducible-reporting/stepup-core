#!/usr/bin/env python3
from stepup.core.api import run, shq


def duplicate(inp_path, out_path):
    quoted_inp = shq([inp_path])
    command = f"cat {quoted_inp} {quoted_inp} > {shq([out_path])}"
    run(command, shell=True, inp=[inp_path], out=[out_path])


run("echo something > single.txt", shell=True, out=["single.txt"])
duplicate("single.txt", "double.txt")
duplicate("double.txt", "quadruple.txt")
