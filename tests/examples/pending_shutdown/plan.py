#!/usr/bin/env python3
from stepup.core.api import run

run("stepup shutdown; sleep 3; echo hello > msg.txt", shell=True, out="msg.txt")
run("cat msg.txt", inp="msg.txt")
