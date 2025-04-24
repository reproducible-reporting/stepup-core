#!/usr/bin/env python3
from stepup.core.api import call, copy, script, static

static("README NOW.txt", "call is cool.py", "script is cool.py")
copy("README NOW.txt", "white space leaves a lot of room for mistakes.txt")
call("call is cool.py", text="abcd")
script("script is cool.py")
