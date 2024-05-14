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
"""Command-line interface to manually clean up output files and/or directories."""

import argparse

from .director import get_socket
from .rpc import SocketSyncRPCClient


def main():
    """Main program."""
    args = parse_args()
    # Forcibly discover the socket and set the client,
    # also when STEPUP_DIRECTOR_SOCKET is not set.
    import stepup.core.api

    stepup.core.api.RPC_CLIENT = SocketSyncRPCClient(get_socket())
    # Only import cleanup after modifying RPC_CLIENT, which is a little hacky.
    from .interact import cleanup

    numf, numd = cleanup(*args.paths)
    print(f"Removed {numf} files and {numd} directories.")


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="cleanup", description="Recursively remove outputs of a file or in a directory."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="A list of paths to consider for the cleanup. "
        "Given a file, all depending outputs will be cleaned. "
        "The file itself is also removed if it is not static. "
        "Given a directory, all containing outputs will be removed. "
        "The directory itself is also removed if it is not static. "
        "The cleanup is performed recursively: outputs of outputs are also removed, etc.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
