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

from hashlib import blake2b

import msgpack
import pytest
from path import Path

from stepup.core.hash import FileHash, compute_file_digest


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


def test_symbolic_link(path_tmp: Path):
    path_dest = path_tmp / "dest.txt"
    with open(path_dest, "w") as fh:
        fh.write("Hello!")
    assert compute_file_digest(path_dest) == blake2b(b"Hello!").digest()
    path_symlink = path_tmp / "link.txt"
    path_symlink.symlink_to("dest.txt")
    assert compute_file_digest(path_symlink) == blake2b(b"Hello!").digest()
    assert compute_file_digest(path_symlink, follow_symlinks=False) == blake2b(b"dest.txt").digest()


def test_hash_dir(path_tmp: Path):
    with pytest.raises(IOError):
        compute_file_digest(path_tmp)


def test_hash_symbolic_link_dir(path_tmp: Path):
    path_sub = path_tmp / "sub"
    path_sub.mkdir()
    path_symlink = path_tmp / "link"
    path_symlink.symlink_to("sub", target_is_directory=True)
    with pytest.raises(IOError):
        assert compute_file_digest(path_symlink)
    assert compute_file_digest(path_symlink, follow_symlinks=False) == blake2b(b"sub").digest()
