#!/usr/bin/env python3
from stepup.core.api import mkdir, static, step

static("../../common/", "../../common/script.py")
mkdir("../public")
step("./script.py", workdir="../../common")
