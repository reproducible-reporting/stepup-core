#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("sleep 5; echo hello > msg.txt", out="msg.txt")
runsh("cat msg.txt", inp="msg.txt")
