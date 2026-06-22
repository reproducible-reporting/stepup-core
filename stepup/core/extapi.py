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
"""Utilities for developers of StepUp extension packages.

These functions are not intended for end-users writing `plan.py` files.
They are meant for authors of new StepUp extensions who need to interact with the director,
filter step dependencies, or implement custom API functions that handle environment variables.
"""

import contextlib
import os
import sys
from collections.abc import Callable, Collection, Iterator

from path import Path

from .utils import CaseSensitiveTemplate, mynormpath, myrealpath, myrelpath

__all__ = (
    "filter_dependencies",
    "get_local_import_paths",
    "subs_env_vars",
)


@contextlib.contextmanager
def subs_env_vars() -> Iterator[Callable[[str | None], Path | None]]:
    """A context manager for substituting environment variables and tracking the used variables.

    The context manager yields a function, `subs`, which takes a string with variables and
    returns the substituted form.
    All used variables are recorded and sent to the director with `amend(env=...)`.
    For example:

    ```python
    with subs_env_vars() as subs:
        path_inp = subs(path_inp)
        path_out = subs(path_out)
    ```

    This function may be used in other API functions to substitute environment variables in
    all relevant paths.
    """
    from stepup.core.api import amend  # noqa: PLC0415

    env_vars = set()

    def subs(path: str | None) -> Path | None:
        if path is None:
            return None
        template = CaseSensitiveTemplate(path)
        if not template.is_valid():
            raise ValueError("The path contains invalid shell variable identifiers.")
        mapping = {}
        for name in template.get_identifiers():
            if name.startswith("*"):
                mapping[name] = f"${{{name}}}"
            else:
                value = os.getenv(name)
                if value is None:
                    raise ValueError(f"Undefined shell variable: {name}")
                mapping[name] = value
                env_vars.add(name)
        result = path if len(mapping) == 0 else template.substitute(mapping)
        return mynormpath(result)

    yield subs
    amend(env=env_vars)


def filter_dependencies(paths: Collection[str]) -> set[Path]:
    """Select path retained by the `${STEPUP_PATH_FILTER}`.

    Parameters
    ----------
    paths
        A collection of paths to filter.
        Relative paths are assumed to be relative to the current working directory.

    Returns
    -------
    filtered_paths
        A collection of paths relative to `${STEPUP_ROOT}` that were retained by the filter.
    """
    # The getenv function from StepUp amends the current step to depend on the variable,
    # to make sure that all steps using it get re-executed properly.
    from stepup.core.api import getenv  # noqa: PLC0415

    # Parse the ${STEPUP_PATH_FILTER} environment variable.
    filter_str = getenv("STEPUP_PATH_FILTER", "-venv")
    filter_str += ":+.:-/"
    rules = []
    stepup_root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
    for filter_item in filter_str.split(":"):
        if filter_item == "":
            continue
        if filter_item.startswith("+"):
            keep = True
        elif filter_item.startswith("-"):
            keep = False
        else:
            raise ValueError(f"Invalid filter item: {filter_item}")
        prefix = filter_item[1:]
        if not prefix.startswith("/"):
            prefix = myrealpath(stepup_root / prefix)
        rules.append((prefix, keep))

    # Filter paths according to the rules.
    result = set()
    realpwd = myrealpath(os.getcwd())
    for path in paths:
        abspath = myrealpath(path)
        for prefix, keep in rules:
            if abspath.startswith(prefix):
                if keep:
                    result.add(myrelpath(abspath, realpwd))
                break
        else:
            raise AssertionError(f"No matching rule found for path: {path}")
    return result


def get_local_import_paths(script_path: Path | None = None) -> list[str]:
    """Get all local files from `sys.modules`.

    Files are only included if they match the `${STEPUP_PATH_FILTER}` environment variable.
    Non-existing files will be ignored, as they can only be the result of a dynamically created
    module, as in issue https://github.com/reproducible-reporting/stepup-core/issues/21
    There is no risk of missing files that still need to be created,
    as all imports have already been successfully resolved already at this point.
    """

    def iter_module_paths():
        for module in sys.modules.values():
            mod_path = getattr(module, "__file__", None)
            if not (mod_path is None or mod_path.startswith("<")):
                mod_path = mynormpath(mod_path)
                if mod_path.exists():
                    yield mod_path

    mod_paths = filter_dependencies(iter_module_paths())
    # The script path is already included in the inputs.
    if script_path is not None:
        mod_paths.discard(mynormpath(script_path))
    return sorted(mod_paths)
