#!/usr/bin/env python3
from stepup.core.api import mkdir, runpy, static

static("../../common/", "../../common/script.py")
mkdir("../public")
runpy("./script.py", workdir="../../common")
