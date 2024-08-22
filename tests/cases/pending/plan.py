#!/usr/bin/env python
from stepup.core.api import step

info = step("cp -v first.txt second.txt", inp="first.txt", out="second.txt", vol="third.txt")

# Tests for the info object, only useful for testing
if info.inp != ["first.txt"]:
    raise AssertionError("Incorrect info.inp")
if info.env != []:
    raise AssertionError("Incorrect info.env")
if info.out != ["second.txt"]:
    raise AssertionError("Incorrect info.out")
if info.vol != ["third.txt"]:
    raise AssertionError("Incorrect info.vol")
if info.filter_vol("*.txt").single() != "third.txt":
    raise AssertionError("Wrong info.filter_vol")
