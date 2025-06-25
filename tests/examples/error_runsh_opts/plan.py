#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("ls --this-option-does-not-exist")
