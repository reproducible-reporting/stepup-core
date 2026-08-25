# Custom Tools

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

A tool is a subcommand of the StepUp CLI.

A StepUp tool can be called from the command line like any other console script with
`stepup <tool> <args>`.
Such tools are different in scope from console scripts:
they interact directly with the internals of StepUp and
require at least `.stepup/graph.db` to be present in the current working directory
(or under the `${STEPUP_ROOT}` directory if that variable is set).
Unless you specifically need this low-level access,
prefer traditional Python [console scripts](console_scripts.md) for your extensions.

A new StepUp tool is created by defining two functions and registering them as entry points.
The examples below assume that you want to add a tool called `fancy` to the StepUp CLI.

1. Write a Python function that implements the tool, using a fixed signature.
   For example, a tool registered as the `fancy` subcommand should have the signature:

    ```python
    import argparse


    def fancy_tool(args: argparse.Namespace) -> None: ...
    ```

    The `args` argument is a `Namespace` object that contains the command-line arguments
    passed to the tool.
    This signature is also available as the type alias `stepup.core.tool.ToolFunc`.

    A tool does not return a return code.
    It reports a mistake that the user can fix by raising `ToolError`,
    which `stepup` turns into a short message on standard error,
    ending the command with return code `1`:

    ```python
    from stepup.core.exceptions import ToolError


    def fancy_tool(args: argparse.Namespace) -> None:
        if not args.path.is_file():
            raise ToolError(f"File does not exist: {args.path}")
    ```

    Any other exception keeps its traceback,
    because it points at a bug rather than at something the user can act on.
    Setting `STEPUP_DEBUG` also shows the traceback of a `ToolError`.

    A tool that must end the command with a return code of its own calls `sys.exit`,
    the way `stepup build` reports the outcome of a build.
    This is not how an error is reported, because raising already covers that case.

2. Write a second function that registers the argument parser,
   again with a fixed signature:

    ```python
    import argparse
    from stepup.core.config_loader import ConfigLoader
    from stepup.core.tool import ToolFunc


    def fancy_subcommand(subparsers, loader: ConfigLoader) -> ToolFunc:
        parser = subparsers.add_parser(
            "fancy",
            help="Description of the tool",
        )
        parser.add_argument(...)
        ...
        loader.patch_parser(parser)
        return fancy_tool
    ```

    The `subparsers` argument is the sub-parsers object from the main `stepup` argument parser.
    The `loader` argument is a `ConfigLoader` instance that can be used to patch the parser
    with configuration file values (see existing tools in `stepup.core` for examples).
    The section it reads is the last word of the parser's `prog`, here `[fancy]`.
    Patching never raises on a bad configuration:
    the problems are collected and reported by `stepup` itself,
    before it calls the tool, see [Configuration](../reference/configuration.md).

3. Create an entry point in `pyproject.toml` pointing to this function:

    ```toml
    [project.entry-points."stepup.tools"]
    fancy = "your.package:fancy_subcommand"
    ```

    where you replace `your.package` with the name of the module that contains
    `fancy_subcommand`.
    The name of the entry point is the name of the subcommand,
    so it must be identical to the name passed to `subparsers.add_parser`.
    StepUp refuses to start when the two differ,
    because the subcommand would otherwise be unreachable.

StepUp never imports these two functions directly:
it only loads the registration function through the entry point,
which in turn hands it the function implementing the tool.
Both must therefore remain importable from the module named in the entry point,
even though nothing seems to use them.
