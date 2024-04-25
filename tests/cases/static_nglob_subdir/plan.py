#!/usr/bin/env python
from stepup.core.api import static, plan

static("sub/", "sub/plan.py")
plan("sub/")
