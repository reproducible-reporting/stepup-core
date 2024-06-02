#!/usr/bin/env python
from stepup.core.api import copy, glob

glob("sub/**", _defer=True)
copy("sub/message.txt", "./")
