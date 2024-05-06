# StepUp Script extends StepUp with scripts that combine planning and running.
# Copyright (C) 2024 Toon Verstraelen
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
"""Support for scripts that combine planning and execution."""

import argparse
import inspect
import os
import sys
from collections.abc import Iterator
from typing import Any

import attrs
from parse import parse
from path import Path

__all__ = ("driver",)


def driver(obj=None) -> int:
    """Driver function to be called from a script as `driver()` or `driver(obj)`.

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
        # Local import because the StepUp client is not always needed.
        from stepup.core.api import amend, static, step

        # Get paths for top-level local imports of the script.
        # These are treated as dependencies for the run function of the script.
        top_mod_paths = get_local_import_paths(script_path)

        # Plan the script.
        if wrapper.has_single:
            if not amend(inp=top_mod_paths):
                return 0
            static_paths, inp_paths, out_paths = wrapper.get_plan()
            inp_paths.extend(top_mod_paths)
            inp_paths.append(script_path)
            static(*static_paths)
            step(f"./{script_path} run", inp=inp_paths, out=out_paths, optional=args.optional)
        if wrapper.has_cases:
            # First collect all cases
            cases = list(wrapper.generate_cases())
            # Include local imports, used to generate the cases, as inputs for the plan step.
            plan_mod_paths = get_local_import_paths(script_path)
            if not amend(inp=plan_mod_paths):
                return 0
            # Then create steps for all cases
            for case_args, case_kwargs in cases:
                argstr = wrapper.format(*case_args, **case_kwargs)
                static_paths, inp_paths, out_paths = wrapper.get_case_plan(
                    *case_args, **case_kwargs
                )
                inp_paths.extend(top_mod_paths)
                inp_paths.append(script_path)
                static(*static_paths)
                step(
                    f"./{script_path} run -- '{argstr}'",
                    inp=inp_paths,
                    out=out_paths,
                    optional=args.optional,
                )
        return 0
    elif args.cmd == "cases":
        if wrapper.has_single:
            print(f"./{script_path} run")
        if wrapper.has_cases:
            for case_args, case_kwargs in wrapper.generate_cases():
                argstr = wrapper.format(*case_args, **case_kwargs)
                print(f"./{script_path} run -- '{argstr}'")
        return 0
    elif args.cmd == "run":
        if args.string == "":
            if not wrapper.has_single:
                raise RuntimeError(f"Script has no info function: {script_path}")
            info = wrapper.filter_info(wrapper.get_info())
            return wrapper.run(**info)
        else:
            if not wrapper.has_cases:
                raise RuntimeError(f"Script has no case_info function: {script_path}")
            case_args, case_kwargs = wrapper.parse(args.string)
            info = wrapper.filter_info(wrapper.get_case_info(*case_args, **case_kwargs))
            return wrapper.run(**info)
    else:
        raise NotImplementedError


def parse_args(script_path: str) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog=script_path, description=f"StepUp driver for script {script_path}."
    )
    sub_parsers = parser.add_subparsers(dest="cmd")
    sub_parsers.required = True

    # plan
    plan_parser = sub_parsers.add_parser(
        "plan",
        description="Submit a plan for executing the script to the director.",
    )
    plan_parser.add_argument(
        "-o", "--optional", default=False, action="store_true", help="Make the run steps optional."
    )

    # cases
    sub_parsers.add_parser("cases", description="Print all ways to run the script.")

    # run
    run_parser = sub_parsers.add_parser("run", description="Print all ways to run the script.")
    # run_parser.set_defaults(cmd=Action.RUN)
    run_parser.add_argument(
        "string",
        default="",
        nargs="?",
        help="A string describing the parameters with which to run the script",
    )

    return parser.parse_args()


@attrs.define
class ScriptWrapper:
    """A wrapper for the script being planned and executed."""

    _object: Any = attrs.field()
    _script_path: Path = attrs.field()
    _has_single: bool = attrs.field(init=False)
    _has_cases: bool = attrs.field(init=False)

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
        assert self._has_single
        info = self._object.info()
        if not isinstance(info, dict) or len(info) == 0:
            raise TypeError("info must return a non-empty dictionary.")
        return info

    def get_plan(self) -> tuple[list[str], list[str], list[str]]:
        info = self.get_info()
        return (
            _get_path_list("static", info, self._script_path, "info"),
            _get_path_list("inp", info, self._script_path, "info"),
            _get_path_list("out", info, self._script_path, "info"),
        )

    def generate_cases(self) -> Iterator[tuple[list, dict]]:
        assert self._has_cases
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
        assert self._has_cases
        if not isinstance(self._object.CASE_FMT, str):
            raise TypeError("CASE_FMT must be a string")
        return self._object.CASE_FMT.format(*args, **kwargs)

    def parse(self, argstr: str) -> tuple[tuple, dict]:
        assert self._has_cases
        case_fmt = self._object.CASE_FMT
        result = parse(case_fmt, argstr, case_sensitive=True)
        if result is None:
            raise ValueError(f"Could not parse string '{argstr}' with CASE_FMT '{case_fmt}'.")
        return result.fixed, result.named

    def get_case_info(self, *args, **kwargs) -> dict:
        assert self._has_cases
        info = self._object.case_info(*args, **kwargs)
        if not isinstance(info, dict) or len(info) == 0:
            raise TypeError("case_info must return a non-empty dictionary.")
        return info

    def get_case_plan(self, *args, **kwargs) -> tuple[list[str], list[str], list[str]]:
        info = self.get_case_info(*args, **kwargs)
        return (
            _get_path_list("static", info, self._script_path, "case_info"),
            _get_path_list("inp", info, self._script_path, "case_info"),
            _get_path_list("out", info, self._script_path, "case_info"),
        )

    def filter_info(self, info: dict) -> dict:
        run_signature = inspect.signature(self._object.run)
        return {key: value for key, value in info.items() if key in run_signature.parameters}

    def run(self, **kwargs) -> int:
        return self._object.run(**kwargs)


def _get_path_list(name: str, info: dict, script_path: str, func_name: str) -> list[str]:
    """Get and check a list of paths from an info dictionary."""
    paths = info.get(name, [])
    if isinstance(paths, str):
        return [paths]
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise TypeError(f"{name} is not a list of strings in function {func_name} in {script_path}")
    return paths


def get_local_import_paths(script_path: Path) -> list[str]:
    # Get paths for all local imports
    stepup_root = Path(os.environ.get("STEPUP_ROOT", Path.cwd())).normpath() / ""
    mod_paths = set()
    for module in sys.modules.values():
        mod_path = getattr(module, "__file__", None)
        if mod_path is not None and mod_path.startswith(stepup_root):
            mod_paths.add(Path(mod_path).relpath())

    # Amend the inputs of the planner with the local imports
    mod_paths.discard(script_path)
    return sorted(mod_paths)
