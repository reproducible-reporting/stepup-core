# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.hash"""

import os
import threading
from hashlib import sha256

import attrs
import pytest
from conftest import TrippingEvent
from path import Path

from stepup.core.exceptions import HashCancelledError, HashFailedError
from stepup.core.hash import (
    HASH_CHUNK_SIZE,
    FileHash,
    compute_file_digest,
    compute_inp_hashes,
    compute_out_hashes,
)


def test_new():
    file_hash = FileHash.unknown()
    assert file_hash.digest == b"u"


def test_simple():
    init_hash = FileHash.unknown()
    new_hash1 = init_hash.refreshed("README.md")
    assert new_hash1 is not init_hash
    assert isinstance(new_hash1.digest, bytes)
    assert new_hash1.digest != b"u"
    assert new_hash1.size > 0
    new_hash2 = new_hash1.refreshed("README.md")
    assert new_hash2 is new_hash1
    new_hash3 = new_hash2.refreshed("pyproject.toml")
    assert new_hash3 is not new_hash2


def test_missing():
    non_existing = "sdfkjaskdfjsadksasdsdfoasudfioausdfosuadfyoa"
    init_hash = FileHash.unknown()
    new_hash = init_hash.refreshed(non_existing)
    assert new_hash is init_hash
    assert new_hash.digest == b"u"
    assert new_hash.size == 0


def test_symbolic_link(path_tmp: Path):
    path_dest = path_tmp / "dest.txt"
    with open(path_dest, "w") as fh:
        fh.write("Hello!")
    assert compute_file_digest(path_dest) == sha256(b"Hello!").digest()
    path_symlink = path_tmp / "link.txt"
    path_symlink.symlink_to("dest.txt")
    assert compute_file_digest(path_symlink) == sha256(b"Hello!").digest()
    assert compute_file_digest(path_symlink, follow_symlinks=False) == sha256(b"dest.txt").digest()


def test_hash_wrong_dir(path_tmp: Path):
    with pytest.raises(HashFailedError):
        compute_file_digest(path_tmp)


def test_refreshed_dir(path_tmp: Path):
    with pytest.raises(HashFailedError):
        FileHash.unknown().refreshed(path_tmp)


def test_refreshed_trailing_sep_on_file(path_tmp: Path):
    path = path_tmp / "file.txt"
    path.write_bytes(b"content")
    # os.stat rejects the trailing separator, which refreshed() reports as an unknown hash.
    init_hash = FileHash.unknown()
    assert init_hash.refreshed(path + os.sep) is init_hash


def test_hash_symbolic_link_dir(path_tmp: Path):
    path_sub = path_tmp / "sub"
    path_sub.mkdir()
    path_symlink = path_tmp / "link"
    path_symlink.symlink_to("sub", target_is_directory=True)
    with pytest.raises(HashFailedError):
        compute_file_digest(path_symlink)
    assert compute_file_digest(path_symlink, follow_symlinks=False) == sha256(b"sub").digest()


def test_digest_cancelled_before_reading(path_tmp: Path):
    path = path_tmp / "small.txt"
    path.write_bytes(b"tiny")
    cancel_event = threading.Event()
    cancel_event.set()
    with pytest.raises(HashCancelledError):
        compute_file_digest(path, cancel_event=cancel_event)


def test_digest_cancelled_mid_file(path_tmp: Path):
    path = path_tmp / "big.bin"
    path.write_bytes(os.urandom(HASH_CHUNK_SIZE * 3 + 123))
    # The chunked loop polls once per chunk, so this trips before EOF.
    cancel_event = TrippingEvent(2)
    with pytest.raises(HashCancelledError):
        compute_file_digest(path, cancel_event=cancel_event)
    assert cancel_event.polls > cancel_event.trip_after


def test_digest_with_and_without_cancel_event(path_tmp: Path):
    data = os.urandom(HASH_CHUNK_SIZE * 3 + 123)
    path = path_tmp / "big.bin"
    path.write_bytes(data)
    expected = sha256(data).digest()
    assert compute_file_digest(path) == expected
    assert compute_file_digest(path, cancel_event=threading.Event()) == expected


def test_refreshed_cancelled_changed_file(path_tmp: Path):
    path = path_tmp / "file.txt"
    path.write_bytes(b"content")
    cancel_event = threading.Event()
    cancel_event.set()
    with pytest.raises(HashCancelledError):
        FileHash.unknown().refreshed(path, cancel_event)


def test_refreshed_cancelled_unchanged_file(path_tmp: Path):
    path = path_tmp / "file.txt"
    path.write_bytes(b"content")
    file_hash = FileHash.unknown().refreshed(path)
    cancel_event = threading.Event()
    cancel_event.set()
    with pytest.raises(HashCancelledError):
        file_hash.refreshed(path, cancel_event)


def test_to_json_unknown():
    assert FileHash.unknown().to_json() is None


def test_from_json_none():
    assert FileHash.from_json(None) == FileHash.unknown()


def test_to_json_from_json_round_trip():
    file_hash = FileHash(sha256(b"foo").digest(), 0o644, 100, 1234.5, 0x8000000000000001)
    restored = FileHash.from_json(file_hash.to_json())
    assert restored == file_hash
    # `==` on FileHash ignores mtime and inode (eq=False), so check those explicitly too.
    assert restored.mtime == file_hash.mtime
    assert restored.inode == file_hash.inode


def test_stat_differs():
    file_hash = FileHash(sha256(b"foo").digest(), 0o644, 100, 1234.5, 42)
    assert not file_hash.stat_differs(file_hash)
    touched = attrs.evolve(file_hash, mtime=2345.6)
    moved = attrs.evolve(file_hash, inode=43)
    # These compare equal because `==` ignores mtime and inode, yet the stat differs.
    assert touched == file_hash
    assert moved == file_hash
    assert file_hash.stat_differs(touched)
    assert file_hash.stat_differs(moved)


def test_stat_differs_unknown():
    """Two unknown hashes never differ, which `Workflow.update_file_hash` relies on."""
    assert not FileHash.unknown().stat_differs(FileHash.unknown())


def test_compute_inp_hashes_cancelled_during_second_file(path_tmp: Path):
    path1 = path_tmp / "inp1.bin"
    path1.write_bytes(b"small input")
    path2 = path_tmp / "inp2.bin"
    path2.write_bytes(os.urandom(HASH_CHUNK_SIZE * 3 + 123))
    # The first file consumes two polls in its digest loop (it fits in one chunk, plus
    # the EOF read), so this trips inside the digest loop of the second file.
    cancel_event = TrippingEvent(4)
    with pytest.raises(HashCancelledError):
        compute_inp_hashes(
            {str(path1): FileHash.unknown(), str(path2): FileHash.unknown()},
            cancel_event=cancel_event,
        )
    assert cancel_event.polls > cancel_event.trip_after


def test_compute_inp_hashes_uncancelled(path_tmp: Path):
    path = path_tmp / "inp.bin"
    path.write_bytes(b"some input")
    inp_result = compute_inp_hashes({str(path): FileHash.unknown()}, cancel_event=threading.Event())
    # The file was unknown before, so its (now known) hash is reported as an unexpected change.
    assert len(inp_result.messages) == 1
    assert str(path) in inp_result.messages[0]
    assert inp_result.new_hashes == inp_result.all_hashes
    assert list(inp_result.all_hashes) == [str(path)]
    assert not inp_result.all_hashes[str(path)].is_unknown


def test_compute_inp_and_out_hashes(path_tmp: Path):
    path_inp = path_tmp / "inp.txt"
    path_inp.write_bytes(b"input")
    path_out = path_tmp / "out.txt"
    path_out.write_bytes(b"output")

    inp_result = compute_inp_hashes(
        {str(path_inp): FileHash.unknown()}, cancel_event=threading.Event()
    )
    # The file was unknown before, so its (now known) hash is reported as an unexpected change.
    assert len(inp_result.messages) == 1
    assert str(path_inp) in inp_result.messages[0]
    assert inp_result.new_hashes == inp_result.all_hashes
    assert list(inp_result.new_hashes) == [str(path_inp)]

    out_result = compute_out_hashes(
        {str(path_out): FileHash.unknown()}, cancel_event=threading.Event()
    )
    assert out_result.messages == []
    assert list(out_result.all_hashes) == [str(path_out)]
