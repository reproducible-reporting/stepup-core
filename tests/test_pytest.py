# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for the helpers in `stepup.core.pytest` that normalize the output of examples."""

import pytest

from stepup.core.path import Path
from stepup.core.pytest import (
    STDERR_BEGIN,
    STDERR_END,
    OutputRule,
    finalize_output,
    mask_file_modes,
    normalize_reporter_output,
    remove_digests,
    select_output_rule,
    slash_traceback_files,
)

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


# The same traceback as printed by Python 3.11 and 3.14, with one frame per Python feature:
# a call spanning several lines, carets under part of a line, and carets under the whole line.
TRACEBACK_311 = """\
Traceback (most recent call last):
  File "/home/me/tb.py", line 16, in <module>
    main()
  File "/home/me/tb.py", line 7, in main
    result = serve(
             ^^^^^^
  File "/home/me/tb.py", line 2, in serve
    return a + b * c[d]
                   ~^^^
IndexError: list index out of range
"""

TRACEBACK_314 = """\
Traceback (most recent call last):
  File "/home/me/tb.py", line 16, in <module>
    main()
    ~~~~^^
  File "/home/me/tb.py", line 7, in main
    result = serve(
        1,
    ...<2 lines>...
        3,
    )
  File "/home/me/tb.py", line 2, in serve
    return a + b * c[d]
                   ~^^^
IndexError: list index out of range
"""

TRACEBACK_NORMALIZED = """\
Traceback (most recent call last):
  File "/home/me/tb.py", line ---, in <module>
    main()
  File "/home/me/tb.py", line ---, in main
    result = serve(
  File "/home/me/tb.py", line ---, in serve
    return a + b * c[d]
IndexError: list index out of range
"""


@pytest.mark.parametrize("text", [TRACEBACK_311, TRACEBACK_314])
def test_normalize_reporter_output_traceback(text: str):
    assert normalize_reporter_output(text) == TRACEBACK_NORMALIZED


def test_normalize_reporter_output_keeps_other_lines():
    # Lines that end in whitespace and lines with carets outside a traceback are not touched.
    text = "   START │ echo    \n    ^^^^\n  SUCCESS │ echo\n"
    assert normalize_reporter_output(text) == text


def test_normalize_reporter_output_run():
    text = "\n".join(
        [
            "DIRECTOR │ Listening on /tmp/stepup-abc/director (StepUp Core 4.1.0)",
            "   START │ ./plan.py",
            STDERR_BEGIN,
            "Some text that depends on the platform",
            STDERR_END,
            "DIRECTOR │ Wall 0.1s, Director 0.1u/0.0s, Steps 0.0u/0.0s",
            "",
        ]
    )
    assert normalize_reporter_output(text) == "\n".join(
        ["   START │ ./plan.py", STDERR_BEGIN, "(stripped)", STDERR_END, ""]
    )


def test_remove_digests():
    text = "\n".join(
        [
            "file:plan.py",
            "               state = CONFIRMED",
            "              digest = " + " ".join(["0123abcd"] * 8),
            "step:./plan.py",
            "          inp_digest = " + " ".join(["4567ef01"] * 8),
            "          out_digest = " + " ".join(["89abcdef"] * 8),
            "             creator   root:",
            "",
        ]
    )
    assert remove_digests(text) == "\n".join(
        [
            "file:plan.py",
            "               state = CONFIRMED",
            "step:./plan.py",
            "             creator   root:",
            "",
        ]
    )


def test_finalize_output(monkeypatch: pytest.MonkeyPatch, path_tmp: Path):
    workdir = path_tmp / "example"
    monkeypatch.chdir(path_tmp)
    text = f"Changing to {workdir}/sub  \nFile {path_tmp}/plan.py\t\n\n\n"
    assert finalize_output(text, workdir) == "Changing to ${CASE}/sub\nFile ${PWD}/plan.py"


def test_select_output_rule():
    first = OutputRule("_stdout1.txt", normalize=str.upper)
    second = OutputRule("_stdout*.txt", mask=str.lower)
    rules = (first, second)
    assert select_output_rule(rules, "_stdout1.txt") is first
    assert select_output_rule(rules, "_stdout2.txt") is second
    # The match is case-sensitive on every platform.
    for suffix in "_graph.txt", "_STDOUT.txt":
        rule = select_output_rule(rules, suffix)
        assert rule.normalize("Text") == "Text"
        assert rule.mask("Text") == "Text"


def test_output_rule_validates():
    with pytest.raises(TypeError):
        OutputRule("_stdout*.txt", normalize="not callable")
