# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Dispatch driver for scripts invoked by `call()`.

The `driver()` function is the explicit entry point.
"""

import argparse
import inspect
import json
import os
import shlex
import sys
from typing import Any, get_type_hints

from path import Path
from rich.console import Console

from .api import loadns
from .cattrs import json_converter

__all__ = ("driver",)


def driver() -> None:
    """Dispatch a function by name when a script is invoked via `call()`.

    Add this to every worker script called via `call()`:

    ```python
    if __name__ == "__main__":
        driver()
    ```

    When invoked with no function argument,
    `driver()` prints one suggested command line per function the script exposes,
    which is useful for discovery.
    """
    ns = inspect.currentframe().f_back.f_globals
    script_path = ns.get("__file__", sys.argv[0])
    args = _parse_args(script_path)
    if args.function is None:
        _print_list(script_path, ns)
    else:
        _dispatch(script_path, ns, args)


def _parse_args(script_path: str) -> argparse.Namespace:
    """Parse command line arguments for `driver()`."""
    parser = argparse.ArgumentParser(
        prog=script_path,
        description="Dispatch a function by name.",
    )
    parser.add_argument(
        "function",
        nargs="?",
        default=None,
        help="Name of the function to invoke.",
    )
    parser.add_argument(
        "json_inp",
        nargs="?",
        default=None,
        help="JSON string of keyword arguments (mutually exclusive with --inp).",
    )
    parser.add_argument(
        "--inp",
        dest="path_inp",
        default=None,
        metavar="PATH",
        help="Path to a Python, JSON, TOML or YAML file of keyword arguments "
        "(mutually exclusive with positional JSON).",
    )
    args = parser.parse_args()
    if args.json_inp is not None and args.path_inp is not None:
        parser.error("Cannot use both positional JSON and --inp.")
    return args


def _dispatch(script_path: str, obj: dict[str, Any], args: argparse.Namespace) -> None:
    """Dispatch a function by name with keyword arguments from JSON or a file."""
    fn = obj.get(args.function)
    if fn is None or not callable(fn):
        raise AttributeError(f"{script_path} does not define a function '{args.function}'")

    if args.json_inp is not None:
        all_kwargs = json.loads(args.json_inp)
    elif args.path_inp is not None:
        all_kwargs = vars(loadns(args.path_inp))
    else:
        all_kwargs = {}

    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        fn(**_structure_kwargs(all_kwargs, hints, script_path))
    else:
        extra = {k for k in all_kwargs if k not in sig.parameters and k not in ("inp", "out")}
        if extra:
            raise TypeError(
                f"{script_path}: function '{fn.__name__}' received unexpected arguments: "
                + ", ".join(sorted(extra))
            )
        filtered = {k: v for k, v in all_kwargs.items() if k in sig.parameters}
        fn(**_structure_kwargs(filtered, hints, script_path))


def _structure_kwargs(
    kwargs: dict[str, Any], hints: dict[str, Any], script_path: str
) -> dict[str, Any]:
    """Structure keyword arguments according to type hints."""
    result = {}
    for k, v in kwargs.items():
        ann = hints.get(k)
        if ann is None:
            result[k] = v
        else:
            try:
                result[k] = json_converter.structure(v, ann)
            except Exception as exc:
                raise TypeError(
                    f"{script_path}: argument '{k}' expected {ann}, got {type(v).__name__}: {v!r}"
                ) from exc
    return result


def _print_list(script_path: str, ns: dict[str, Any]) -> None:
    """Print one suggested command line per callable function in the script."""
    if "__all__" in ns:
        registry = {name: fn for name, fn in ns.items() if name in ns["__all__"] and callable(fn)}
    else:
        module_name = ns.get("__name__")
        registry = {
            name: fn
            for name, fn in ns.items()
            if not name.startswith("_")
            and callable(fn)
            and (module_name is None or getattr(fn, "__module__", None) == module_name)
        }
    console = Console(highlight=False)
    display = _short_path(script_path)
    for fn_name, fn in registry.items():
        sig = inspect.signature(fn)
        params = {
            name: (None if param.default is inspect.Parameter.empty else param.default)
            for name, param in sig.parameters.items()
            if param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        }
        console.print(
            f"[cyan]{display}[/] [yellow]{fn_name}[/] [grey50]{shlex.quote(json.dumps(params))}[/]"
        )


def _short_path(script_path: str) -> str:
    """Return a short relative path to the script."""
    rel = Path(script_path).relpath()
    return rel if os.sep in rel else "." / rel
