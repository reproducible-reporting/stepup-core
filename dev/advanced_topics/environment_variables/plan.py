#!/usr/bin/env python
from stepup.core.api import step

step("echo ${MYVAR}", env="MYVAR")
