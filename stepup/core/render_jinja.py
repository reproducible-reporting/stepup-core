# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Rendering of template files with Jinja2.

Variables for the template are loaded from files with [`loadns()`][stepup.core.api.loadns],
and may be complemented with a JSON string on the command line.
"""

import argparse
import json
from collections.abc import Sequence
from typing import Any

import jinja2
from path import Path

from .api import amend, loadns
from .constants import RENDER_JINJA_MODES
from .extapi import get_local_import_paths
from .path import StrPath, coerce_str

__all__ = ("main", "render_jinja_file", "render_jinja_str")


def main(argv: Sequence[str] | None = None) -> None:
    """Command-line entry point for the `render-jinja` console script."""
    args = _parse_args(argv)
    latex = _resolve_latex(args.mode, args.path_out)
    dir_out = args.path_out.parent.absolute()
    variables = vars(loadns(*args.paths_variables, dir_out=dir_out, do_amend=False))
    # The local imports are only known after the Python variable files have been executed.
    amend(inp=get_local_import_paths())
    if args.json is not None:
        variables.update(json.loads(args.json))
    result = render_jinja_file(args.path_in, variables, latex=latex)
    with open(args.path_out, "w") as fh:
        fh.write(result)
    # A rendered script must remain executable, so the template's mode is copied over.
    args.path_out.chmod(args.path_in.stat().st_mode)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line arguments of the `render-jinja` console script."""
    parser = argparse.ArgumentParser(
        prog="sc-render-jinja",
        description="Render a file with Jinja2.",
    )
    parser.add_argument("path_in", type=Path, help="The input file")
    parser.add_argument(
        "paths_variables",
        nargs="*",
        type=Path,
        help="Python, JSON, TOML or YAML files defining variables. "
        "They are loaded in the given order, "
        "so later variable definitions may overrule earlier ones. "
        "Python files have the advantage of supporting more types and logic. "
        "path.Path instances are interpreted as relative to the parent of the variable file.",
    )
    parser.add_argument("path_out", type=Path, help="The output file")
    parser.add_argument(
        "--mode",
        choices=RENDER_JINJA_MODES,
        help="The delimiter style to use",
        default="auto",
    )
    parser.add_argument(
        "--json",
        help="Variables are given as a JSON string (overrules the variables defined in files)",
    )
    return parser.parse_args(argv)


def _resolve_latex(mode: str, path_out: StrPath) -> bool:
    """Decide whether angle-style delimiters are used, given the mode and the output path."""
    if mode == "auto":
        return Path(path_out).suffix == ".tex"
    return mode == "latex"


def render_jinja_file(path: StrPath, variables: dict[str, Any], *, latex: bool = False) -> str:
    """Render a template file with Jinja2 and return the result.

    Parameters
    ----------
    path
        The filename of the template to load.
    variables
        A dictionary of variables to substitute into the template.
    latex
        When `True`, angle-style delimiters are used, e.g. `<%` instead of `{%`.

    Returns
    -------
    rendered
        A string with the result.
    """
    with open(path) as fh:
        source = fh.read()
    return render_jinja_str(source, variables, latex=latex, name=coerce_str(path))


def render_jinja_str(
    source: str,
    variables: dict[str, Any],
    *,
    latex: bool = False,
    name: str = "<template>",
) -> str:
    """Render a template string with Jinja2 and return the result.

    Parameters
    ----------
    source
        The template as a string.
    variables
        A dictionary of variables to substitute into the template.
    latex
        When `True`, angle-style delimiters are used, e.g. `<%` instead of `{%`.
    name
        How the template is identified in error messages and tracebacks.

    Returns
    -------
    rendered
        A string with the result.
    """
    # Customize the Jinja2 environment
    env_kwargs = {
        "keep_trailing_newline": True,
        "trim_blocks": True,
        "undefined": jinja2.StrictUndefined,
        "autoescape": False,
    }
    if latex:
        env_kwargs.update(
            {
                "block_start_string": "<%",
                "block_end_string": "%>",
                "variable_start_string": "<<",
                "variable_end_string": ">>",
                "comment_start_string": "<#",
                "comment_end_string": "#>",
                "line_statement_prefix": "%==",
            }
        )
    env = jinja2.Environment(**env_kwargs)

    # Load template, assign filename (for tracebacks) and render.
    template = env.from_string(source)
    template.filename = name
    # The variables are passed as a dictionary, not as keyword arguments,
    # because a variable named `self` would collide with the first argument of `render`.
    return template.render(variables)


if __name__ == "__main__":
    main()
