# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.render_jinja."""

import jinja2
import pytest
from path import Path

from stepup.core.render_jinja import _resolve_latex, render_jinja_file, render_jinja_str


def test_plain_delimiters():
    assert render_jinja_str("Hello {{ name }}!", {"name": "Alice"}) == "Hello Alice!"


def test_latex_delimiters():
    template = "%== for name in names\nHi << name >>.\n%== endfor\n"
    result = render_jinja_str(template, {"names": ["Alice", "Bob"]}, latex=True)
    assert result == "Hi Alice.\nHi Bob.\n"


def test_latex_leaves_curly_brackets_alone():
    assert render_jinja_str(r"\section{<< title >>}", {"title": "Intro"}, latex=True) == (
        r"\section{Intro}"
    )


def test_undefined_is_strict():
    with pytest.raises(jinja2.UndefinedError):
        render_jinja_str("Hello {{ name }}!", {})


def test_name_shows_up_in_traceback():
    with pytest.raises(jinja2.UndefinedError) as exc_info:
        render_jinja_str("{{ name }}", {}, name="my_template")
    assert any(str(frame.path) == "my_template" for frame in exc_info.traceback)


def test_variable_named_self():
    # A variable whose name collides with an argument of `jinja2.Template.render`
    # must not break the rendering of the others.
    # (Jinja2 reserves `self` inside the template, so the variable itself is not reachable.)
    assert render_jinja_str("{{ x }}", {"self": "me", "x": 1}) == "1"


def test_trailing_newline_is_kept():
    assert render_jinja_str("{{ x }}\n", {"x": 1}) == "1\n"


def test_render_file(path_tmp: Path):
    path_template = path_tmp / "template.txt"
    path_template.write_text("Hello {{ name }}!\n")
    assert render_jinja_file(path_template, {"name": "Alice"}) == "Hello Alice!\n"


def test_render_file_name_shows_up_in_traceback(path_tmp: Path):
    path_template = path_tmp / "template.txt"
    path_template.write_text("{{ name }}\n")
    with pytest.raises(jinja2.UndefinedError) as exc_info:
        render_jinja_file(path_template, {})
    assert any(str(frame.path) == str(path_template) for frame in exc_info.traceback)


@pytest.mark.parametrize(
    ("mode", "path_out", "latex"),
    [
        ("auto", "report.tex", True),
        ("auto", "report.md", False),
        ("plain", "report.tex", False),
        ("latex", "report.md", True),
    ],
)
def test_resolve_latex(mode: str, path_out: str, latex: bool):
    assert _resolve_latex(mode, path_out) is latex
