#!/usr/bin/env python3
from stepup.core.api import getenv

print(getenv("HERE", back=True))
print(getenv("DELAYED", back=True))
