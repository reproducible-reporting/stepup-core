#!/usr/bin/env python3
from stepup.core.api import mkdir, runsh, static

static("../../common/", "../../common/script.py")
mkdir("../public")
runsh("./script.py", workdir="../../common")
