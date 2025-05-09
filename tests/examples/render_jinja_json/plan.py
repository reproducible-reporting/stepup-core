#!/usr/bin/env python3
from stepup.core.api import glob, render_jinja, static

static("template.txt")
for path_variables in glob("trip*.json"):
    render_jinja("template.txt", path_variables, f"rendered-{path_variables.stem}.txt")
