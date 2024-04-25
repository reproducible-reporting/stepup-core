#!/usr/bin/env python
from stepup.core.api import step, pool

pool("test", 2)
step("sleep 0.1; echo A", pool="test")
step("sleep 0.1; echo B", pool="test")
step("sleep 0.1; echo C", pool="test")
