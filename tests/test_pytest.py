# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for the helpers in `stepup.core.pytest` that normalize the output of examples."""

import pytest

from stepup.core.pytest import mask_file_modes, slash_traceback_files

# The three blocks are parallel: line i of each block describes the same reporter output,
# as written on POSIX, as written on Windows, and after masking.
POSIX = """\
 UPDATED │ work.py (mode -rw-r--r-- ➜ -rwxr-xr-x)
 UPDATED │ inp.txt (digest UNKNOWN ➜ 5f2c85fe, size 0 ➜ 6, mode ?--------- ➜ -rw-r--r--)
Modified out hash    copy.txt (digest 2d0abe4a ➜ UNKNOWN, mode -rw-r--r-- ➜ ?---------)
Modified step hash   explained, inp_digest 649fbfbd ➜ 7d90c6ec, out_digest 23dab281 ➜ (unset)
Same step hash       compact, inp_digest 649fbfbd, out_digest 23dab281
""".splitlines()

WINDOWS = """\
 UPDATED │ work.py (mode -rw-rw-rw- ➜ -rw-rw-rw-)
 UPDATED │ inp.txt (digest UNKNOWN ➜ 5f2c85fe, size 0 ➜ 6, mode ?--------- ➜ -rw-rw-rw-)
Modified out hash    copy.txt (digest 2d0abe4a ➜ UNKNOWN, mode -rw-rw-rw- ➜ ?---------)
Modified step hash   explained, inp_digest 0a1b2c3d ➜ 4e5f6a7b, out_digest 8c9d0e1f ➜ (unset)
Same step hash       compact, inp_digest 0a1b2c3d, out_digest 8c9d0e1f
""".splitlines()

MASKED = """\
 UPDATED │ work.py (mode -********* ➜ -*********)
 UPDATED │ inp.txt (digest UNKNOWN ➜ 5f2c85fe, size 0 ➜ 6, mode ?--------- ➜ -*********)
Modified out hash    copy.txt (digest 2d0abe4a ➜ UNKNOWN, mode -********* ➜ ?---------)
Modified step hash   explained, inp_digest ******** ➜ ********, out_digest ******** ➜ (unset)
Same step hash       compact, inp_digest ********, out_digest ********
""".splitlines()


@pytest.mark.parametrize(
    ("posix", "windows", "masked"), list(zip(POSIX, WINDOWS, MASKED, strict=True))
)
def test_mask_file_modes(posix: str, windows: str, masked: str):
    assert mask_file_modes(posix) == masked
    assert mask_file_modes(windows) == masked


@pytest.mark.parametrize(
    "text",
    [
        # The digest of a file does not depend on permission bits.
        "Modified inp hash    subs1.txt (digest 5f2c85fe ➜ 2d0abe4a)",
        # Not the reporter output about a mode.
        "   START │ chmod +x plan.py  # mode -rw-r--r--",
        " SUCCESS │ ./plan.py",
    ],
)
def test_mask_file_modes_keeps(text: str):
    assert mask_file_modes(text) == text


def test_slash_traceback_files():
    text = "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "C:\\Users\\me\\project\\plan.py", line 4, in <module>',
            '    raise ValueError("a\\b")',
            '      File "sub\\work.py", line 1',
            'ValueError: File "a\\b" is not a traceback line',
        ]
    )
    assert slash_traceback_files(text) == "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "C:/Users/me/project/plan.py", line 4, in <module>',
            '    raise ValueError("a\\b")',
            '      File "sub/work.py", line 1',
            'ValueError: File "a\\b" is not a traceback line',
        ]
    )


def test_slash_traceback_files_keeps_posix():
    text = 'Traceback (most recent call last):\n  File "/home/me/plan.py", line 4, in <module>'
    assert slash_traceback_files(text) == text
