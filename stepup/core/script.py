# StepUp Script extends StepUp with scripts that combine planning and running.
# © 2024–2025 Toon Verstraelen
#
# This file is part of StepUp Script.
#
# StepUp Script is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Script is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Driver function to facilitate writing scripts that adhere to StepUp's script protocol.

See [Script Protocol](../getting_started/script_single.md) for more details.
"""

import argparse
import inspect
import shlex
import sys
from collections.abc import Iterator
from typing import Any

import attrs
from parse import parse
from path import Path

from .stepinfo import dump_step_info
from .utils import format_command

__all__ = ("driver",)


@attrs.define
class ScriptWrapper:
    """A wrapper for the script being planned and executed."""

    _object: Any = attrs.field()
    """
    The object containing the functions to plan and run the script.
    This can be a module whose functions are used or an object whose methods are used.
    """

    _script_path: Path = attrs.field()
    """The path of the script relative to the work directory."""

    _has_single: bool = attrs.field(init=False)
    """Whether the script has functions for a single execution: `info` and `run`."""

    _has_cases: bool = attrs.field(init=False)
    """
    Whether the script has functions for multiple executions:
    `CASE_FMT`, `cases`, `case_info` and `run`.
    """

    @_has_single.default
    def _default_has_singe(self) -> bool:
        get_info = getattr(self._object, "info", None)
        if get_info is None:
            return False
        if not callable(get_info):
            raise TypeError(f"The info object in {self._script_path} is not callable.")
        return True

    @_has_cases.default
    def _default_has_cases(self) -> bool:
        case_fmt = getattr(self._object, "CASE_FMT", None)
        generate_cases = getattr(self._object, "cases", None)
        get_case_info = getattr(self._object, "case_info", None)
        missing = [case_fmt is None, generate_cases is None, get_case_info is None]
        if all(missing):
            return False
        if any(missing):
            raise TypeError(
                f"The script {self._script_path} must either define all or none of the following: "
                "CASE_FMT, cases and case_info"
            )
        if not isinstance(case_fmt, str):
            raise TypeError(f"The CASE_FMT in {self._script_path} is not a string.")
        if not callable(generate_cases):
            raise TypeError(f"The cases object in {self._script_path} is not callable.")
        if not callable(get_case_info):
            raise TypeError(f"The case_info object in {self._script_path} is not callable.")
        return True

    def __attrs_post_init__(self):
        if not hasattr(self._object, "run"):
            raise TypeError(f"Script {self._script_path} has no run function.")
        if not (self._has_single or self._has_cases):
            raise TypeError(f"The script {self._script_path} has neither info nor case_info.")

    @property
    def has_single(self) -> bool:
        """Does the script have a info() function? (single run)"""
        return self._has_single

    @property
    def has_cases(self) -> bool:
        """Does the script have a case_info() function? (run several cases)"""
        return self._has_cases

    # Wrapper methods

    def get_info(self) -> dict:
        """Get the return value of the `info` function."""
        if not self._has_single:
            raise NotImplementedError("get_info only works for scripts with an info function")
        info = self._object.info()
        if not isinstance(info, dict):
            raise TypeError("info must return a dictionary.")
        if not all(isinstance(key, str) for key in info):
            raise TypeError("All keys of the info dict must be strings.")
        return info

    def get_plan(self) -> tuple[list[str], list[str], list[str]]:
        """Return a tuple with normalized information from the info dictionary (single run).

        Returns
        -------
        static_paths
            A list of files that (only) this script declares to be static.
            When static files are used by multiple steps, take care to declare it static only once.
        inp_paths
            A list of input paths.
        out_paths
            A list of output paths.
        """
        info = self.get_info()
        return (
            _get_path_list("static", info, self._script_path, "info"),
            _get_path_list("inp", info, self._script_path, "info"),
            _get_path_list("out", info, self._script_path, "info"),
        )

    def generate_cases(self) -> Iterator[tuple[list, dict]]:
        """Run the `cases` generator and normalize the iterates.

        Yields
        ------
        args
            Unnamed arguments for the `info` function.
        kwargs
            Named arguments for the `info` function.
        """
        if not self._has_cases:
            raise NotImplementedError("generate_cases only works for scripts with multiple cases")
        for case in self._object.cases():
            if isinstance(case, dict):
                yield [], case
            elif (
                isinstance(case, tuple)
                and len(case) == 2
                and isinstance(case[0], list | tuple)
                and isinstance(case[1], dict)
            ):
                yield case
            elif isinstance(case, list | tuple):
                yield list(case), {}
            else:
                yield [case], {}

    def format(self, *args, **kwargs) -> str:
        """Convert a (normalized) iterate from the `cases` function into a string."""
        if not self._has_cases:
            raise NotImplementedError("format only works for scripts with multiple cases")
        if not isinstance(self._object.CASE_FMT, str):
            raise TypeError("CASE_FMT must be a string")
        return self._object.CASE_FMT.format(*args, **kwargs)

    def parse(self, argstr: str) -> tuple[tuple, dict]:
        """Convert a string back into `args` and `kwargs`. (Inverse of `format`.)"""
        if not self._has_cases:
            raise NotImplementedError("parse only works for scripts with multiple cases")
        case_fmt = self._object.CASE_FMT
        result = parse(case_fmt, argstr, case_sensitive=True)
        if result is None:
            raise ValueError(f"Could not parse string '{argstr}' with CASE_FMT '{case_fmt}'.")
        return result.fixed, result.named

    def get_case_info(self, *args, **kwargs) -> dict:
        """Get the return value of the `info` function."""
        if not self._has_cases:
            raise NotImplementedError("get_case_info only works for scripts with multiple cases")
        info = self._object.case_info(*args, **kwargs)
        if not isinstance(info, dict):
            raise TypeError("case_info must return a dictionary.")
        if not all(isinstance(key, str) for key in info):
            raise TypeError("All keys of the info dict must be strings.")
        return info

    def get_case_plan(self, *args, **kwargs) -> tuple[list[str], list[str], list[str]]:
        """Return a tuple with normalized information from the info dictionary (multiple runs).

        Returns
        -------
        static_paths
            A list of files that (only) this script declares to be static.
            When static files are used by multiple steps, take care to declare it static only once.
        inp_paths
            A list of input paths.
        out_paths
            A list of output paths.
        """
        info = self.get_case_info(*args, **kwargs)
        return (
            _get_path_list("static", info, self._script_path, "case_info"),
            _get_path_list("inp", info, self._script_path, "case_info"),
            _get_path_list("out", info, self._script_path, "case_info"),
        )

    def filter_info(self, info: dict) -> dict:
        """Return a reduce info dictionary with only the arguments used by the `run` function."""
        run_signature = inspect.signature(self._object.run)
        return {key: value for key, value in info.items() if key in run_signature.parameters}

    def run(self, **filtered_info):
        """Call the `run` function."""
        self._object.run(**filtered_info)


def driver(obj: Any = None):
    """Implement script protocol.

    The most common usage is to call `driver()` from a script
    that defines `info()` and `run()` function, e.g.:

    ```python
    #!/usr/bin/env python3
    from stepup.core.script import driver

    def info():
        return {"inp": ["input.txt"], "out": ["output.txt"]}

    def run(inp: str, out: str):
        with open(inp) as fh:
            text = fh.read()
        with open(out, "w") as fh:
            fh.write(text.upper())

    if __name__ == "__main__":
        driver()
    ```

    Parameters
    ----------
    obj
        When not provided, the namespace of the module where `driver` is defined
        will be searched for names like 'info' and 'run' to implement the script protocol.
        When an object is given as a parameter, its attributes are searched instead.
    """
    frame = inspect.currentframe().f_back
    script_path = Path(frame.f_locals["__file__"]).relpath()
    if obj is None:
        # Get the calling module and use it as obj
        module_name = frame.f_locals["__name__"]
        obj = sys.modules.get(module_name)
        if obj is None:
            raise ValueError(
                f"The driver must be called from an imported module, got {module_name}"
            )
    args = parse_args(script_path)
    wrapper = ScriptWrapper(obj, script_path)
    if args.cmd == "plan":
        _driver_plan(script_path, args, wrapper)
    elif args.cmd == "cases":
        _driver_cases(script_path, wrapper)
    elif args.cmd == "run":
        _driver_run(script_path, args, wrapper)
    else:
        raise NotImplementedError


def parse_args(script_path: str) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog=script_path, description=f"StepUp script driver for {script_path}."
    )
    sub_parsers = parser.add_subparsers(dest="cmd")
    sub_parsers.required = True

    # plan
    plan_parser = sub_parsers.add_parser(
        "plan",
        description="Submit a plan for executing the script to the director.",
    )
    plan_parser.add_argument(
        "--step-info", help="Path to a file where step information of the run steps is written."
    )
    plan_parser.add_argument(
        "-o", "--optional", default=False, action="store_true", help="Make the run steps optional."
    )

    # cases
    sub_parsers.add_parser("cases", description="Print all ways to run the script.")

    # run
    run_parser = sub_parsers.add_parser("run", description="Print all ways to run the script.")
    run_parser.add_argument(
        "string",
        default="",
        nargs="?",
        help="A string describing the parameters with which to run the script",
    )

    return parser.parse_args()


def _driver_plan(script_path: str, args: argparse.Namespace, wrapper: ScriptWrapper):
    """Create the step to plan the run part of the script."""
    # Local import because the StepUp client is not always needed.
    from stepup.core.api import runpy, runsh, static

    runfunc = runpy if script_path.endswith(".py") else runsh

    # Plan the script.
    step_info = None
    if wrapper.has_single:
        static_paths, inp_paths, out_paths = wrapper.get_plan()
        inp_paths.append(script_path)
        static(*static_paths)
        command = format_command(script_path) + " run"
        step_info = runfunc(command, inp=inp_paths, out=out_paths, optional=args.optional)
    if wrapper.has_cases:
        # First collect all cases
        cases = list(wrapper.generate_cases())
        # Then create steps for all cases
        step_info = [] if step_info is None else [step_info]
        for case_args, case_kwargs in cases:
            argstr = shlex.quote(wrapper.format(*case_args, **case_kwargs))
            if argstr.startswith("-"):
                argstr = f"-- {argstr}"
            static_paths, inp_paths, out_paths = wrapper.get_case_plan(*case_args, **case_kwargs)
            inp_paths.append(script_path)
            static(*static_paths)
            command = format_command(script_path) + " run " + argstr
            step_info.append(
                runfunc(
                    command,
                    inp=inp_paths,
                    out=out_paths,
                    optional=args.optional,
                )
            )
    if args.step_info is not None:
        dump_step_info(args.step_info, step_info)


def _driver_cases(script_path: str, wrapper: ScriptWrapper):
    """Print all commands on script that can be used to run the script."""
    if wrapper.has_single:
        print(f"./{script_path} run")
    if wrapper.has_cases:
        for case_args, case_kwargs in wrapper.generate_cases():
            argstr = shlex.quote(wrapper.format(*case_args, **case_kwargs))
            if argstr.startswith("-"):
                argstr = f"-- {argstr}"
            print(f"./{script_path} run {argstr}")


def _driver_run(script_path: str, args: argparse.Namespace, wrapper: ScriptWrapper):
    """Call the `run` function with the appropriate arguments."""
    # Local import because the StepUp client is not always needed.
    if args.string == "":
        if not wrapper.has_single:
            raise RuntimeError(f"Script has no info function: {script_path}")
        info = wrapper.filter_info(wrapper.get_info())
        wrapper.run(**info)
        return
    if not wrapper.has_cases:
        raise RuntimeError(f"Script has no case_info function: {script_path}")
    case_args, case_kwargs = wrapper.parse(args.string)
    info = wrapper.filter_info(wrapper.get_case_info(*case_args, **case_kwargs))
    wrapper.run(**info)


def _get_path_list(name: str, info: dict, script_path: str, func_name: str) -> list[str]:
    """Get and check a list of paths from an info dictionary."""
    paths = info.get(name, [])
    if isinstance(paths, str):
        return [paths]
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise TypeError(f"{name} is not a list of strings in function {func_name} in {script_path}")
    return paths
