#!/usr/bin/env python3
from decimal import Decimal

from stepup.core.api import call, static

static("work.py")
work = call("work.py", a=Decimal("2.0"), b=Decimal("2.2"), fmt="json")
