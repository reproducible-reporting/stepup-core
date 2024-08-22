# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Unit tests for stepup.core.api."""

from stepup.core.api import StepInfo


def test_step_info():
    info = StepInfo(
        "key",
        ["one.txt", "two.txt", "three.py"],
        ["AAA", "BBB"],
        ["out1.pdf", "out1.log"],
        ["vol1.aaa", "vol1.bbb", "hhh.cde"],
    )
    assert info.inp[1] == "two.txt"
    assert info.env[0] == "AAA"
    assert info.out[-1] == "out1.log"
    assert info.vol[2] == "hhh.cde"
    assert list(info.filter_inp("*.txt")) == ["one.txt", "two.txt"]
    assert list(info.filter_inp("${*pre}.*", pre="t*").files()) == ["three.py", "two.txt"]
    assert list(info.filter_out("out1.*")) == ["out1.log", "out1.pdf"]
    assert list(info.filter_out("${*pre}.pdf", pre="out?").files()) == ["out1.pdf"]
    assert list(info.filter_vol("???.???")) == ["hhh.cde"]
    assert list(info.filter_vol("*.${*l}${*l}${*l}", l="?").files()) == ["vol1.aaa", "vol1.bbb"]
