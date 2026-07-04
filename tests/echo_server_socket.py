# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""A single echo RPC server over stdio pipes, used by test_rpc.py"""

import asyncio
import sys

from core_common import EchoHandler

from stepup.core.rpc import serve_socket_rpc


async def main():
    handler = EchoHandler("socket")
    await serve_socket_rpc(handler, sys.argv[1], handler.stop_event)


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
