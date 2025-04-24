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
"""Utilities for testing with pytest."""

import asyncio
import os
import re
import shutil
import subprocess
import sys

from path import Path

__all__ = ("remove_hashes", "run_example")


def remove_hashes(graph: dict) -> dict:
    """Remove hashes from a JSON representation of the workflow."""
    for node in graph["nodes"]:
        node.pop("h", None)
    return graph


STDERR_BEGIN = "──────────────────────────────── Standard error ────────────────────────────────"
STDERR_END = "────────────────────────────────────────────────────────────────────────────────"


async def run_example(srcdir: Path, tmpdir: Path, overwrite_expected=False):
    """Run an example use case in a temporary directory and check the outputs.

    The script ``main.sh`` in the example is the entry point for the test case.
    It must have one or more lines ``stepup ...  & # > current_stdout{something}.txt &``,
    from which the substring `& # ` is removed before testing, to keep the output for comparison
    to the expected reporter.

    All files in the srcdir starting with ``expected_`` will be compared to corresponding files
    starting with ``current_`` after completion of the example.

    Parameters
    ----------
    srcdir
        The source directory of the example.
    tmpdir
        The temporary directory where the example is executed.
    overwrite_expected
        Update the expected outputs in the source with the outputs from the tmpdir.
    """
    workdir = tmpdir / "example"
    shutil.copytree(srcdir, workdir)

    # Rewrite the script to redirect the input and output of stepup.
    sed_proc = await asyncio.create_subprocess_shell(
        "sed -i -e '/^stepup/ s/ & #//' main.sh",
        stdin=subprocess.DEVNULL,
        cwd=workdir,
    )
    await sed_proc.wait()
    assert sed_proc.returncode == 0
    stepup_proc = await asyncio.create_subprocess_shell(
        "./main.sh",
        stdin=subprocess.DEVNULL,
        cwd=workdir,
        env=os.environ | {"PYTHONUNBUFFERED": "yes", "COLUMNS": "80", "STEPUP_DEBUG": "1"},
    )
    try:
        async with asyncio.timeout(30):
            await stepup_proc.wait()

        pairs = []

        for path_exp in sorted(workdir.glob("expected*.*")):
            fn_exp = path_exp.basename()
            path_cur = workdir / ("current" + fn_exp[8:])
            with open(path_cur) as fh:
                cur = fh.read().rstrip()
            # Normalize output before comparing:
            cur = cur.replace(Path.cwd(), "${PWD}")
            cur = cur.replace(workdir, "${CASE}")
            # - Listening paths are random
            cur = re.sub(r"        0/0 \|   DIRECTOR │ Listening on .*\n", "", cur)
            # - Exact line numbers in exceptions change often, not important
            cur = re.sub(r", line \d+, in ", ", line ---, in ", cur)
            # - Remove new types of traceback output not present in Python 3.11
            cur = re.sub(r"^    \.{3}<\d+ lines>\.{3}\n", "", cur, flags=re.MULTILINE)
            cur = re.sub(r"^    \)\n", "", cur, flags=re.MULTILINE)
            cur = re.sub(r"    \~*\^*\n", "", cur, flags=re.MULTILINE)
            # - Remove trailing whitespace
            cur = re.sub(r"[ \t]+?(\n|\Z)", r"\1", cur)
            # - Remove digests, change often, content of results must be tested explicitly.
            cur = re.sub(r" {10}(.{4})digest = [ 0-9a-f]{71}\n {21}= [ 0-9a-f]{71}\n", "", cur)
            # - Remove standard error: sensitive to OS and Python version
            cur = re.sub(
                STDERR_BEGIN + r".*?" + STDERR_END,
                STDERR_BEGIN + "\n(stripped)\n" + STDERR_END,
                cur,
                flags=re.DOTALL,
            )

            # Perform the comparison
            print()
            print(f"########## {fn_exp} ##########")
            print()
            print(cur)
            if overwrite_expected:
                path_exp = srcdir / fn_exp
                with open(path_exp, "w") as fh:
                    print(cur, file=fh)
            else:
                with open(path_exp) as fh:
                    exp = fh.read().rstrip()
                pairs.append((path_exp, cur, exp))
    finally:
        for path_log in sorted(workdir.glob(".stepup/*.log")):
            print()
            print(f"########## {path_log} ##########")
            print()
            with open(path_log) as fh:
                print(fh.read().rstrip())

    # Check late for errors, to maximize the printed output.
    for path_exp, cur, exp in pairs:
        assert cur == exp, path_exp

    assert stepup_proc.returncode == 0


async def run_plan(srcdir: Path, tmpdir: Path):
    """Copy a plan.py script to a temporary directory and run it as an ordinary Python script.

    Parameters
    ----------
    srcdir
        The source directory of the example, with a `plan.py` script.
    tmpdir
        The temporary directory to use.

    Notes
    -----
    This is not the intended way of using `plan.py` scripts.
    They are normally processed by StepUp instead of running them directly.
    Nevertheless, they should still work without generating exceptions, for debugging purposes.
    """
    workdir = tmpdir / "example"
    shutil.copytree(srcdir, workdir)
    plan_proc = await asyncio.create_subprocess_shell(
        f"{sys.executable} plan.py",
        stdin=subprocess.DEVNULL,
        cwd=workdir,
        env=os.environ | {"PYTHONUNBUFFERED": "yes"},
    )
    await plan_proc.wait()
    assert plan_proc.returncode == 0
