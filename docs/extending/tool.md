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

2. Create an additional new Python function that implements the argument parsers.
   This function should have a fixed signature:

    ```python
    import argparse

    def custom_subcommand(subparser: argparse.ArgumentParser) -> callable:
        parser = subparser.add_parser(
            "name",
            help="Description of the tool",
        )
        parser.add_argument(...)
        ...
        return custom_tool
    ```

3. Create an entry point in `pyproject.toml` pointing to this function:

    ```toml
    [project.entry-points."stepup.tools"]
    custom = "your.package:custom_subcommand"
    ```

    where you replace `your.package` with the name of the module that contains `custom_command`.
