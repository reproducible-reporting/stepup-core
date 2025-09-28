#!/usr/bin/env python3
from stepup.core.api import pool, runsh

pool("test", 2)
runsh("sleep 0.1; echo A", pool="test")
runsh("sleep 0.1; echo B", pool="test")
runsh("sleep 0.1; echo C", pool="test")
