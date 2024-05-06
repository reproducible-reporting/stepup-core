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
"""A single echo RPC server over stdio pipes, used by test_rpc.py"""

import asyncio

from core_common import EchoHandler

from stepup.core.rpc import serve_stdio_rpc


async def main():
    handler = EchoHandler("stdio")
    await serve_stdio_rpc(handler)


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
