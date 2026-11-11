#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp=["ping.txt", "pong.txt"])
with (
    open("ping.txt", encoding="utf-8", newline="\n") as ping,
    open("pong.txt", encoding="utf-8") as pong,
    open("work.txt", "w", encoding="utf-8") as work,
):
    work.write(ping.read() + pong.read())
