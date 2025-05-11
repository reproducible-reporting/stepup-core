#!/usr/bin/env python3
from stepup.core.api import render_jinja, static

static("email_template.txt", "config.yaml")
render_jinja("email_template.txt", "config.yaml", "email.txt")
