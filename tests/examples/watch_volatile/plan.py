#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("echo blub blub > vol.txt", vol="vol.txt")
