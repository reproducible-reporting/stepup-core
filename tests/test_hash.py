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
"""Unit tests for stepup.core.hash"""

import msgpack

import pytest

from stepup.core.hash import FileHash


def test_new():
    file_hash = FileHash.unknown()
    assert file_hash.digest == b"u"
    data = msgpack.packb(file_hash.unstructure())
    assert file_hash == FileHash.structure(msgpack.unpackb(data))


def test_simple():
    file_hash = FileHash.unknown()
    assert file_hash.update("README.md") is True
    assert file_hash.update("README.md") is False
    assert isinstance(file_hash.digest, bytes)
    assert file_hash.update("README.md") is False
    assert file_hash.update("pyproject.toml") is True
    data = msgpack.packb(file_hash.unstructure())
    assert file_hash == FileHash.structure(msgpack.unpackb(data))


def test_missing():
    non_existing = "sdfkjaskdfjsadksasdsdfoasudfioausdfosuadfyoa"
    file_hash = FileHash.unknown()
    assert file_hash.update(non_existing) is None
    assert file_hash.digest == b"u"
    assert file_hash.update("README.md") is True
    assert file_hash.update("README.md") is False
    digest = file_hash.digest
    assert file_hash.update(non_existing) is None
    assert file_hash.digest == digest
    data = msgpack.packb(file_hash.unstructure())
    assert file_hash == FileHash.structure(msgpack.unpackb(data))


def test_dir():
    file_hash = FileHash.unknown()
    assert file_hash.update("foo/") is None
    assert file_hash.digest == b"u"
    with pytest.raises(ValueError):
        file_hash.update("stepup")
    assert file_hash.update("stepup/") is True
    assert file_hash.digest == b"d"
    assert file_hash.update("stepup/") is False
    data = msgpack.packb(file_hash.unstructure())
    assert file_hash == FileHash.structure(msgpack.unpackb(data))
