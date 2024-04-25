#!/usr/bin/env python
from stepup.core.api import getenv

print(getenv("HERE", is_path=True))
print(getenv("DELAYED", is_path=True))
