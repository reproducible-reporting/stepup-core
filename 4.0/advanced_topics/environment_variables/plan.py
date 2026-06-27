#!/usr/bin/env python3
from stepup.core.api import run

run("echo ${MYVAR}", env="MYVAR", shell=True)
