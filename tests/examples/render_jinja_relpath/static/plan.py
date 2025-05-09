#!/usr/bin/env python3
from stepup.core.api import getenv, render_jinja

PUBLIC = getenv("PUBLIC", back=True)
render_jinja("main.tex", "../variables.py", "variables.py", PUBLIC)
