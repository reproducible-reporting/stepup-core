#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("date > date.txt", vol="date.txt")
