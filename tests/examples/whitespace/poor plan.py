#!/usr/bin/env python3
from stepup.core.api import call, copy, script, static

static("README NOW.md", "call is cool.py", "script is cool.py")
copy("README NOW.md", "white space leaves a lot of room for mistakes.md")
call("call is cool.py", text="abcd")
script("script is cool.py")
