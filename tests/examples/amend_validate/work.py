#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp=["ping.txt", "pong.txt"])
with open("ping.txt") as ping, open("pong.txt") as pong, open("work.txt", "w") as work:
    work.write(ping.read() + pong.read())
