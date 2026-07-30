#!/usr/bin/env python3
from stepup.core.api import copy

# Two hops, so the optional producer of ../hop2.txt is not a direct source
# of the last step in this chain.
copy("../hop2.txt", "hop3.txt")
copy("hop3.txt", "hop4.txt")
