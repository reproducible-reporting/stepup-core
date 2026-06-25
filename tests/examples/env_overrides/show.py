#!/usr/bin/env python3
import os

with open("greeting.txt", "w") as fh:
    print(os.environ["GREETING"], file=fh)
