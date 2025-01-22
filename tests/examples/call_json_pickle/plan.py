#!/usr/bin/env python3
from stepup.core.api import call, getenv, static

static("join.py", "write.py")
join = call(
    "join.py",
    word1="Hello",
    word2="World",
    fmt=getenv("STEPUP_CALL_FORMAT"),
    inp=True,
    out=True,
)
call("write.py", inp=join.out[0])
