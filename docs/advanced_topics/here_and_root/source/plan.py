#!/usr/bin/env python
from stepup.core.api import static, plan, mkdir

static("sub/", "sub/plan.py")
mkdir("../public/")
plan("sub/")
