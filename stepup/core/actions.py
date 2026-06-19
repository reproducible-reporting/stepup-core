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
import sys
from importlib.metadata import entry_points

from stepup.core.config import ConfigLoader

from .worker import WorkThread

__all__ = ("act_subcommand", "act_tool", "copy", "runexec", "runpy", "runpyep", "runsh")


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
    if len(args) == 0:
        raise ValueError("runpy requires at least one argument")
    return work_thread.runpy(args[0], args[1:])


def runpyep(argstr: str, work_thread: WorkThread) -> int:
    """Execute a Python console_script entry point, using the forkserver when available.

    Parameters
    ----------
    argstr
        The entry point command name followed by its arguments as a single string,
        parsed with `shlex.split`.
    work_thread
        The work thread that is executing the command.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    args = shlex.split(argstr)
    if not args:
        raise ValueError("runpyep requires at least one argument")
    return work_thread.runpyep(args[0], args[1:])


def runexec(argstr: str, work_thread: WorkThread) -> int:
    """Execute a command directly (without a shell) and return the returncode.

    Parameters
    ----------
    argstr
        The command and its arguments as a single string, parsed with `shlex.split`.
    work_thread
        The work thread that is executing the command.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    args = shlex.split(argstr)
    if not args:
        raise ValueError("runexec requires at least one argument")
    return work_thread.runexec_verbose(args)


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


def act_tool(args: argparse.Namespace):
    """Execute an action.

    Parameters
    ----------
    args
        The parsed command line arguments.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    # Make a dummy work thread to execute the action.
    work_thread = WorkThread(f"{shlex.quote(args.action_name)} {shlex.join(args.action_args)}")
    work_thread.run()
    sys.exit(work_thread.returncode)


def act_subcommand(subparsers, loader: ConfigLoader) -> callable:
    """Define command-line arguments for the action tool.

    Parameters
    ----------
    subparsers
        The sub parser to add the action tool to.
    loader
        The configuration loader to override the default configuration with
        config file values.
        It is ignored by this function, included for consistency with other subcommand functions.

    Returns
    -------
    callable
        The action function.
    """
    parser = subparsers.add_parser("act", help="Execute an action.")
    action_names = sorted(ep.name for ep in entry_points(group="stepup.actions"))
    parser.add_argument("action_name", help="The action to execute.", choices=action_names)
    parser.add_argument("action_args", help="The arguments for the action.", nargs="*")
    return act_tool
