#!/usr/bin/env python3
from stepup.core.api import render_jinja, static

static("template.txt")
for path_variables in static("trip*.json"):
    render_jinja("template.txt", path_variables, f"rendered-{path_variables.stem}.txt")
