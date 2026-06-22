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
"""Dispatch decorator and driver for scripts invoked by `call()`.

`callme` optionally registers functions for restricted dispatch and list mode.
`driver` is the explicit entry point: add it to every worker script called via `call()`:

```python
if __name__ == "__main__":
    driver()
```
"""

import argparse
import inspect
import json
import os
import shlex
import sys
import typing
from collections.abc import Callable

from rich.console import Console

from .cattrs import json_converter

__all__ = ("callme", "driver")

_registry: dict[str, dict[str, Callable]] = {}


def callme(fn: Callable) -> Callable:
    """Register a function for restricted dispatch and list mode.

    When `@callme` is used, only decorated functions are callable via `driver()`.
    When `@callme` is omitted entirely, `driver()` falls back to dispatching any
    public function in the module by name.

    Parameters
    ----------
    fn
        The function to register.
        It is returned unchanged, so `@callme` is transparent to callers.

    Returns
    -------
    fn
        The original function, unmodified.
    """
    frame = inspect.currentframe().f_back
    module_name = frame.f_globals["__name__"]

    if module_name not in _registry:
        _registry[module_name] = {}

    _registry[module_name][fn.__name__] = fn
    return fn


def driver() -> None:
    """Dispatch a function by name when a script is invoked via `call()`.

    Add this to every worker script called via `call()`:

    ```python
    if __name__ == "__main__":
        driver()
    ```

    When `@callme` decorators are present, only decorated functions are callable.
    When no `@callme` decorators are used, any public (non-underscore) function
    in the module is callable by name.

    When invoked with no function argument, `driver()` prints one suggested command
    per callable function and exits, which is useful for discovery.
    """
    # Use the caller's frame globals to find functions in the no-@callme case.
    # sys.modules["__main__"] is NOT updated by runpy.run_path (used by the forkserver),
    # so it would point to the forkserver process instead of the user script.
    # The caller's frame globals are the script's execution namespace in both cases.
    caller_globals = inspect.currentframe().f_back.f_globals
    module_file = caller_globals.get("__file__", sys.argv[0])
    registry = _registry.get("__main__")
    if registry is not None:
        _dispatch(module_file, registry)
    else:
        _dispatch_plain(module_file, caller_globals)


def _dispatch(script_path: str, registry: dict[str, Callable]) -> None:
    """Dispatch to a function in *registry* by name, using the current sys.argv."""
    args = _parse_args(script_path)

    if args.function is None:
        _print_list(script_path, registry)
        return

    fn = registry.get(args.function)
    if fn is None:
        raise AttributeError(
            f"{script_path} does not define a '@callme' function '{args.function}'"
        )

    _call_fn(fn, args, script_path)


def _dispatch_plain(script_path: str, ns: dict) -> None:
    """Dispatch to any public function in the *ns* namespace by name."""
    args = _parse_args(script_path)

    if args.function is None:
        _print_list(script_path, None, ns)
        return

    fn = ns.get(args.function)
    if fn is None or not callable(fn):
        raise AttributeError(f"{script_path} does not define a function '{args.function}'")

    _call_fn(fn, args, script_path)


def _call_fn(fn: Callable, args: argparse.Namespace, script_path: str) -> None:
    """Load kwargs from args and invoke *fn*."""
    if args.json_inp is not None:
        all_kwargs = json.loads(args.json_inp)
    elif args.path_inp is not None:
        from stepup.core.api import loadns  # noqa: PLC0415

        all_kwargs = vars(loadns(args.path_inp))
    else:
        all_kwargs = {}

    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
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


def _parse_args(script_path: str) -> argparse.Namespace:
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
        help="Path to a JSON/YAML file of kwargs (mutually exclusive with positional JSON).",
    )
    args = parser.parse_args()
    if args.json_inp is not None and args.path_inp is not None:
        parser.error("Cannot use both positional JSON and --inp.")
    return args


def _short_path(script_path: str) -> str:
    rel = os.path.relpath(os.path.abspath(script_path))
    return rel if "/" in rel else f"./{rel}"


def _print_list(
    script_path: str, registry: dict[str, Callable] | None, ns: dict | None = None
) -> None:
    if registry is None and ns is not None:
        if "__all__" in ns:
            registry = {
                name: fn for name, fn in ns.items() if name in ns["__all__"] and callable(fn)
            }
        else:
            module_name = ns.get("__name__")
            registry = {
                name: fn
                for name, fn in ns.items()
                if not name.startswith("_")
                and callable(fn)
                and (module_name is None or getattr(fn, "__module__", None) == module_name)
            }
    if not registry:
        return
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


def _structure_kwargs(kwargs: dict, hints: dict, script_path: str) -> dict:
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
