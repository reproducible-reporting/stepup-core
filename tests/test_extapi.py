# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
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
"""Tests for stepup.core.extapi."""

import contextlib

from stepup.core.extapi import filter_dependencies


def test_filter_dependencies(monkeypatch, path_tmp):
    (path_tmp / "project/sub").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    abs_paths = [path_tmp / "project/foo", path_tmp / "project/sub/down/bar", "/usr/bin/echo"]
    monkeypatch.chdir(path_tmp / "project/sub")
    assert filter_dependencies(abs_paths) == {"../foo", "down/bar"}


def test_filter_dependencies_external(monkeypatch, path_tmp):
    (path_tmp / "project").makedirs()
    (path_tmp / "pkgs").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    monkeypatch.setenv("STEPUP_PATH_FILTER", f"+{path_tmp}/pkgs")
    abs_paths = [path_tmp / "pkgs/helper.py", path_tmp / "other.py"]
    monkeypatch.chdir(path_tmp / "project")
    assert filter_dependencies(abs_paths) == {"../pkgs/helper.py"}


def test_filter_dependencies_external2(monkeypatch, path_tmp):
    (path_tmp / "project").makedirs()
    (path_tmp / "pkgs").makedirs()
    (path_tmp / "templates").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    path_filter = f"+{path_tmp}/pkgs/:+{path_tmp}/templates"
    monkeypatch.setenv("STEPUP_PATH_FILTER", path_filter)
    abs_paths = [
        path_tmp / "pkgs/helper.py",
        path_tmp / "other.py",
        path_tmp / "templates/fancy.typ",
    ]
    monkeypatch.chdir(path_tmp / "project")
    assert filter_dependencies(abs_paths) == {"../pkgs/helper.py", "../templates/fancy.typ"}


def test_filter_dependencies_relative1(monkeypatch, path_tmp):
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    rel_paths = ["foo.txt", "venv/mod.py", "venv2/bar.py", "egg.py"]
    with contextlib.chdir(path_tmp):
        assert filter_dependencies(rel_paths) == {"foo.txt", "egg.py"}


def test_filter_dependencies_relative2(monkeypatch, path_tmp):
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    monkeypatch.setenv("STEPUP_PATH_FILTER", "+../external")
    (path_tmp / "project/sub").makedirs()
    with contextlib.chdir(path_tmp / "project/sub"):
        monkeypatch.setenv("ROOT", path_tmp / "project/")
        monkeypatch.setenv("HERE", "sub/")
        rel_paths = ["foo", "../../external/bar", "../../egg", "../../other/spam"]
        assert filter_dependencies(rel_paths) == {"foo", "../../external/bar"}
