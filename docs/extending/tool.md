# Custom Tools

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

    def fancy_tool(args: argparse.Namespace) -> int:
        ...
    ```

    The `args` argument is a `Namespace` object that contains the command-line arguments
    passed to the tool.

2. Write a second function that registers the argument parser,
   again with a fixed signature:

    ```python
    import argparse
    from collections.abc import Callable
    from stepup.core.config import ConfigLoader

    def fancy_subcommand(subparsers, loader: ConfigLoader) -> Callable:
        parser = subparsers.add_parser(
            "fancy",
            help="Description of the tool",
        )
        parser.add_argument(...)
        ...
        loader.patch_parser(parser, "fancy")
        return fancy_tool
    ```

    The `subparsers` argument is the sub-parsers object from the main `stepup` argument parser.
    The `loader` argument is a `ConfigLoader` instance that can be used to patch the parser
    with configuration file values (see existing tools in `stepup.core` for examples).

3. Create an entry point in `pyproject.toml` pointing to this function:

    ```toml
    [project.entry-points."stepup.tools"]
    fancy = "your.package:fancy_subcommand"
    ```

    where you replace `your.package` with the name of the module that contains
    `fancy_subcommand`.
