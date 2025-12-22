#!/usr/bin/env python3
from stepup.core.api import render_jinja, static

static("template.tex", "variables.py")
render_jinja("template.tex", "variables.py", "rendered.tex")
