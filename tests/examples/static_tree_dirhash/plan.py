#!/usr/bin/env python3
from stepup.core.api import run, static

static("foo")
# StepUp should fail because foo/bar is a directory.
run("echo foo/bar", inp="foo/bar")
