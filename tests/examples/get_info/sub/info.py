#!/usr/bin/env python3
from stepup.core.api import amend, get_info

info = get_info()
assert info.command == "./info.py"
assert info.workdir == "sub"
assert set(info.inp) == {"info.py", "../README.txt", "inp0.txt"}
assert info.env == ["BAR", "FOO"]
assert info.out == ["../out0.txt", "out1.txt"]
assert info.vol == ["../vol1.txt", "vol0.txt"]

with open("out1.txt", "w") as f:
    pass
with open("../out0.txt", "w") as f:
    pass
with open("vol0.txt", "w") as f:
    pass
with open("../vol1.txt", "w") as f:
    pass

amend(inp="../inp1.txt", env="EGG", out="../out2.txt", vol="../vol2.txt")

with open("../out2.txt", "w") as f:
    pass
with open("../vol2.txt", "w") as f:
    pass

# Info should not be affected by amendments.
info = get_info()
assert info.command == "./info.py"
assert info.workdir == "sub"
assert set(info.inp) == {"info.py", "../README.txt", "inp0.txt"}
assert info.env == ["BAR", "FOO"]
assert info.out == ["../out0.txt", "out1.txt"]
assert info.vol == ["../vol1.txt", "vol0.txt"]
