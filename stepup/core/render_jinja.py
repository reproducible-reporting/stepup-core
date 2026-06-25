# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Rendering of template files with Jinja2.

Parameters for the template can be defined in multiple ways:

- Python files with a .py extension
- JSON files with a .json extension
- TOML files with a .toml extension
- YAML files with a .yaml or .yml extension
- As a JSON string on the command line
"""

import argparse
import json

import jinja2
from path import Path

from .extapi import get_local_import_paths

__all__ = ("render_jinja",)


def main() -> None:
    """Command-line entry point for the `render-jinja` console script."""
    parser = argparse.ArgumentParser(
        prog="render-jinja",
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
        "path.Path instances are interpreted as relative to parent of the variable file.",
    )
    parser.add_argument("path_out", type=Path, help="The output file")
    parser.add_argument(
        "--mode",
        choices=["auto", "plain", "latex"],
        help="The delimiter style to use",
        default="auto",
    )
    parser.add_argument(
        "--json",
        help="Variables are given as a JSON string (overrules the variables files)",
    )
    args = parser.parse_args()

    # Local import to delay activation synchronous connection to StepUp directory until needed.
    from stepup.core.api import amend, loadns  # noqa: PLC0415

    if args.mode == "plain":
        latex = False
    elif args.mode == "latex":
        latex = True
    elif args.mode == "auto":
        latex = args.path_out.endswith(".tex")
    else:
        raise ValueError(f"mode not supported: {args.mode}")
    dir_out = Path(args.path_out).parent.absolute()
    variables = vars(loadns(*args.paths_variables, dir_out=dir_out, do_amend=False))
    amend(inp=get_local_import_paths())
    if args.json is not None:
        variables.update(json.loads(args.json))
    # Render the template
    result = render_jinja(args.path_in, variables, latex)
    with open(args.path_out, "w") as fh:
        fh.write(result)
    # Clone the permissions from the input file to the output file
    args.path_out.chmod(args.path_in.stat().st_mode)


def render_jinja(
    path_template: str,
    variables: dict[str, str],
    latex: bool = False,
    *,
    str_in: str | None = None,
) -> str:
    """The template is processed with jinja and returned after filling in variables.

    Parameters
    ----------
    path_template
        The filename of the template to load, may be a mock
    variables
        A dictionary of variables to substitute into the template.
    latex
        When True, the angle-version of the template codes is used, e.g. `<%` etc.
    str_in
        The template string.
        When given path_templates is not loaded and only used for error messages.

    Returns
    -------
    str_out
        A string with the result.
    """
    # Customize Jinja 2 environment
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

    # Load template and use it
    if str_in is None:
        with open(path_template) as f:
            str_in = f.read()
    template = env.from_string(str_in)
    template.filename = path_template
    return template.render(**variables)


if __name__ == "__main__":
    main()
