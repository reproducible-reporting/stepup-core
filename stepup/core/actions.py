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
"""Functions designed to be executed by a worker.

Actions are functions that can be safely executed by a worker,
which means they are subject to some restrictions:

- They should not have side effects (e.g., modifying global variables).
  Importing modules is usually fine.
- They should not use stdin or stdout. (They can write to stderr.)
- The accept a single string parameter.
- They return an integer returncode.
"""

import argparse
import os
import shlex
import shutil
from importlib.metadata import entry_points

from .worker import WorkThread

__all__ = ("copy", "mkdir", "runpy", "runsh")


def runsh(argstr: str, work_thread: WorkThread) -> int:
    """Execute a shell command and return the returncode.

    Parameters
    ----------
    argstr
        The command to execute in the shell.
    work_thread
        The work thread that is executing the command.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    return work_thread.runsh_verbose(argstr)


def runpy(argstr: str, work_thread: WorkThread) -> int:
    """Execute a Python script and return the returncode. Local imports are amended as inputs.

    Parameters
    ----------
    args
        Python script to execute and its arguments split into a list of strings.
    work_thread
        The work thread that is executing the command.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    args = shlex.split(argstr)
    return work_thread.runpy(args[0], args[1:])


def copy(argstr: str) -> int:
    """Copy a file preserving permissions.

    Parameters
    ----------
    args
        The source and destination files.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    args = shlex.split(argstr)
    if len(args) != 2:
        raise ValueError("copy requires exactly two arguments")
    src, dst = args
    shutil.copy2(src, dst)
    st = os.stat(src)
    os.chown(dst, st.st_uid, st.st_gid)
    return 0


def mkdir(argstr: str) -> int:
    """Create a directory.

    Parameters
    ----------
    path
        The path to the directory to create.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    args = shlex.split(argstr)
    if len(args) != 1:
        raise ValueError("mkdir requires exactly one argument")
    os.makedirs(args[0], exist_ok=True)
    return 0


def act_tool(args: argparse.Namespace) -> int:
    """Execute an action.

    Parameters
    ----------
    action
        The action to execute.
    argstr
        The arguments to pass to the action.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    # Make a dummy work thread to execute the action.
    work_thread = WorkThread(f"{shlex.quote(args.action_name)} {shlex.join(args.action_args)}")
    return work_thread.run()


def act_subcommand(subparser: argparse.ArgumentParser) -> callable:
    """Create a subparser for the action tool.

    Parameters
    ----------
    subparser
        The subparser to add the action tool to.

    Returns
    -------
    callable
        The action function.
    """
    parser = subparser.add_parser("act", help="Execute an action.")
    action_names = sorted(ep.name for ep in entry_points(group="stepup.actions"))
    parser.add_argument("action_name", help="The action to execute.", choices=action_names)
    parser.add_argument("action_args", help="The arguments for the action.", nargs="*")
    return act_tool
