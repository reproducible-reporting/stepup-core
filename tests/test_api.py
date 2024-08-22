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

import pytest
from path import Path

from stepup.core.api import StepInfo, translate, translate_back


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
    assert info.filter_inp("*.txt").files() == ("one.txt", "two.txt")
    assert info.filter_inp("${*pre}.*", pre="t*").files() == ("three.py", "two.txt")
    assert info.filter_out("out1.*").files() == ("out1.log", "out1.pdf")
    assert info.filter_out("${*pre}.pdf", pre="out?").single() == "out1.pdf"
    assert info.filter_vol("???.???").single() == "hhh.cde"
    assert info.filter_vol("*.${*l}${*l}${*l}", l="?").files() == ("vol1.aaa", "vol1.bbb")


@pytest.mark.parametrize("with_stepup_root", [True, False])
def test_translate(monkeypatch, with_stepup_root):
    if with_stepup_root:
        monkeypatch.setenv("STEPUP_ROOT", Path.cwd())
    monkeypatch.setenv("HERE", "foo/bar")
    assert translate("somefile.txt") == "foo/bar/somefile.txt"
    assert translate("somefile.txt", "../") == "foo/somefile.txt"
    assert translate("somefile.txt", "/egg/") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "../") == "/somefile.txt"


def test_translate_outside(monkeypatch):
    monkeypatch.setenv("STEPUP_ROOT", Path.cwd() / "foo")
    monkeypatch.setenv("HERE", "bar")
    assert translate("somefile.txt") == "bar/somefile.txt"
    assert translate("somefile.txt", "../") == "somefile.txt"
    assert translate("../../foo/somefile.txt") == "somefile.txt"
    assert translate("somefile.txt", "/egg/") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "../") == "/somefile.txt"


@pytest.mark.parametrize("with_stepup_root", [True, False])
def test_translate_back(monkeypatch, with_stepup_root):
    if with_stepup_root:
        monkeypatch.setenv("STEPUP_ROOT", Path.cwd())
    monkeypatch.setenv("HERE", "foo/bar")
    assert translate_back("foo/bar/somefile.txt") == "somefile.txt"
    assert translate_back("foo/somefile.txt", "../") == "somefile.txt"
    assert translate_back("/egg/somefile.txt", "/egg/") == "somefile.txt"
    assert translate_back("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate_back("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate_back("/somefile.txt", "../") == "/somefile.txt"
