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
"""Unit tests for stepup.core.hash"""

from hashlib import blake2b

import pytest
from path import Path

from stepup.core.hash import FileHash, compute_file_digest


def test_new():
    file_hash = FileHash.unknown()
    assert file_hash.digest == b"u"


def test_simple():
    init_hash = FileHash.unknown()
    new_hash1 = init_hash.regen("README.md")
    assert new_hash1 is not init_hash
    assert isinstance(new_hash1.digest, bytes)
    assert new_hash1.digest != b"u"
    assert new_hash1.size > 0
    new_hash2 = new_hash1.regen("README.md")
    assert new_hash2 is new_hash1
    new_hash3 = new_hash2.regen("pyproject.toml")
    assert new_hash3 is not new_hash2


def test_missing():
    non_existing = "sdfkjaskdfjsadksasdsdfoasudfioausdfosuadfyoa"
    init_hash = FileHash.unknown()
    new_hash = init_hash.regen(non_existing)
    assert new_hash is init_hash
    assert new_hash.digest == b"u"
    assert new_hash.size == 0


def test_dir():
    init_hash = FileHash.unknown()
    dir_hash1 = init_hash.regen("foo/")
    assert dir_hash1.digest == b"u"
    with pytest.raises(ValueError):
        init_hash.regen("stepup")
    dir_hash2 = init_hash.regen("stepup/")
    assert dir_hash2 is not init_hash
    assert dir_hash2.digest == b"d"
    dir_hash3 = dir_hash2.regen("stepup/")
    assert dir_hash3 is dir_hash2


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
        compute_file_digest(path_symlink)
    assert compute_file_digest(path_symlink, follow_symlinks=False) == blake2b(b"sub").digest()
