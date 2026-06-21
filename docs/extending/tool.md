# Custom Tools

A tool is a subcommand of the StepUp CLI.
While it is rarely necessary to create a new tool, you can do so as follows:

1. Create a new Python function that implements the tool.
   This function should have a fixed signature:

    ```python
    import argparse

    def custom_tool(args: argparse.Namespace) -> int:
        ...
    ```

    The `args` argument is a `Namespace` object that contains the command line arguments
    passed to the tool.

2. Create an additional new Python function that registers the argument parser.
   This function should have a fixed signature:

    ```python
    import argparse
    from stepup.core.config import ConfigLoader

    def custom_subcommand(subparsers, loader: ConfigLoader) -> callable:
        parser = subparsers.add_parser(
            "name",
            help="Description of the tool",
        )
        parser.add_argument(...)
        ...
        return custom_tool
    ```

    The `subparsers` argument is the sub-parsers object from the main `stepup` argument parser.
    The `loader` argument is a `ConfigLoader` instance that can be used to patch the parser
    with configuration file values (see existing tools in `stepup.core` for examples).

3. Create an entry point in `pyproject.toml` pointing to this function:

    ```toml
    [project.entry-points."stepup.tools"]
    custom = "your.package:custom_subcommand"
    ```

    where you replace `your.package` with the name of the module that contains `custom_command`.
