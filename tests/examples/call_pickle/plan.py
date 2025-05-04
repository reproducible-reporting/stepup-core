#!/usr/bin/env python3
from decimal import Decimal

from stepup.core.api import call, runsh, static

static("work.py")
work = call("work.py", a=Decimal("2.0"), b=Decimal("2.2"))
assert work.inp == ["work.py", "work_inp.pickle"]
assert work.out == []
runsh(
    'python -c \'import pickle; print(pickle.load(open("work_out.pickle", "rb")))\'',
    inp="work_out.pickle",
)
