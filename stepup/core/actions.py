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
- They should not use stdin or stdout. (They can write to stderr.)
- The accept a single string parameter.
- They return an integer returncode.
"""

from stepup.core.worker import WorkThread

__all__ = ("runsh",)


def runsh(command: str, work_thread: WorkThread) -> int:
    """Execute a shell command and return the returncode.

    Parameters
    ----------
    command
        The shell command with command-line arguments to execute.

    Returns
    -------
    exitcode
        The exit code of the command.
    """
    return work_thread.runsh(command)
