#!/usr/bin/env python3
from stepup.core.api import render_jinja, static

static("template.md", "variables.py")
render_jinja("template.md", "variables.py", "rendered.md")
