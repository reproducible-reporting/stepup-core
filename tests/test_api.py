# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
"""Tests for SteUp Core API."""

import pytest
from path import Path

from stepup.core.api import getenv, loadns


def noop_amend(*_args, **_kwargs):
    pass


def test_getenv_nonexisting(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.delenv("SFDDFHT", raising=False)
    assert getenv("SFDDFHT") is None
    for back in True, False:
        for path in True, False:
            assert len(getenv("SFDDFHT", back=back, path=path, multi=True)) == 0
    with pytest.raises(ValueError):
        getenv("SFDDFHT", back=True)
    with pytest.raises(ValueError):
        getenv("SFDDFHT", path=True)
    with pytest.raises(ValueError):
        getenv("SFDDFHT", path=True, back=True)


@pytest.mark.parametrize("use_default", [True, False])
def test_getenv_single(monkeypatch, use_default):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.setenv("ROOT", "../")
    monkeypatch.setenv("HERE", "work/")
    if use_default:
        monkeypatch.delenv("SFDDFHT", raising=False)
        default = "sub/asdf"
    else:
        monkeypatch.setenv("SFDDFHT", "sub/asdf")
        default = None
    assert getenv("SFDDFHT", default=default) == "sub/asdf"
    p = getenv("SFDDFHT", default=default, back=True)
    assert isinstance(p, Path)
    assert p == Path("../sub/asdf")
    p = getenv("SFDDFHT", default=default, path=True)
    assert isinstance(p, Path)
    assert p == Path("sub/asdf")
    p = getenv("SFDDFHT", default=default, path=True, back=True)
    assert isinstance(p, Path)
    assert p == Path("../sub/asdf")


@pytest.mark.parametrize("use_default", [True, False])
def test_getenv_default_multi1(monkeypatch, use_default):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.setenv("ROOT", "../")
    monkeypatch.setenv("HERE", "work/")
    monkeypatch.delenv("SFDDFHT", raising=False)
    if use_default:
        monkeypatch.delenv("SFDDFHT", raising=False)
        default = "sub/asdf"
    else:
        monkeypatch.setenv("SFDDFHT", "sub/asdf")
        default = None
    for path in True, False:
        ps = getenv("SFDDFHT", default=default, path=path, multi=True)
        assert len(ps) == 1
        assert isinstance(ps[0], Path)
        assert ps[0] == Path("sub/asdf")
        ps = getenv("SFDDFHT", default=default, path=path, multi=True, back=True)
        assert len(ps) == 1
        assert isinstance(ps[0], Path)
        assert ps[0] == Path("../sub/asdf")


@pytest.mark.parametrize("use_default", [True, False])
def test_getenv_default_multi3(monkeypatch, use_default):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.setenv("ROOT", "../")
    monkeypatch.setenv("HERE", "work/")
    if use_default:
        monkeypatch.delenv("SFDDFHT", raising=False)
        default = "sub/asdf:foo:"
    else:
        monkeypatch.setenv("SFDDFHT", "sub/asdf:foo:")
        default = None
    for path in True, False:
        ps = getenv("SFDDFHT", default=default, path=path, multi=True)
        assert len(ps) == 2
        assert isinstance(ps[0], Path)
        assert ps[0] == Path("sub/asdf")
        assert isinstance(ps[1], Path)
        assert ps[1] == Path("foo")
        ps = getenv("SFDDFHT", default=default, path=path, multi=True, back=True)
        assert len(ps) == 2
        assert isinstance(ps[0], Path)
        assert ps[0] == Path("../sub/asdf")
        assert isinstance(ps[1], Path)
        assert ps[1] == Path("../foo")


def test_loadns_py1(path_tmp):
    path_foo = path_tmp / "foo.py"
    with open(path_foo, "w") as fh:
        print("a = 10", file=fh)
    ns = loadns(path_foo)
    assert ns.a == 10


def test_lloadns_py2(path_tmp):
    path_foo = path_tmp / "foo.py"
    with open(path_foo, "w") as fh:
        print("a = 10", file=fh)
    path_bar = path_tmp / "bar.py"
    with open(path_bar, "w") as fh:
        print("from foo import a", file=fh)
    ns = loadns(path_bar)
    assert ns.a == 10
