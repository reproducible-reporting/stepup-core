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
"""Application programming interface to the director.

To keep things simple, it is assumed that one Python process only communicates with one director.

This module should not be imported by other stepup.core modules, safe for some notable exceptions:

- `stepup.core.interact`
- `stepup.core.extapi`
- Inside some functions, e.g. `driver()` in `stepup.core.call`.
"""

import contextlib
import json
import os
import shlex
import sys
import tomllib
from collections.abc import Collection, Iterable
from runpy import run_path
from types import SimpleNamespace

import yaml
from path import Path

from .cattrs import json_converter, yaml_converter
from .enums import Need
from .extapi import subs_env_vars
from .nglob import NGlobMulti
from .rpc import DummySyncRPCClient, SocketSyncRPCClient
from .step import FileHash
from .stepinfo import StepInfo
from .utils import (
    CaseSensitiveTemplate,
    apply_affixes,
    format_command,
    get_affixes,
    make_path_out,
    parse_resources,
    string_to_list,
    translate,
    translate_back,
)

__all__ = (
    "RPC_CLIENT",
    "amend",
    "call",
    "copy",
    "dumpns",
    "getenv",
    "getinfo",
    "glob",
    "graph",
    "loadns",
    "plan",
    "render_jinja",
    "run",
    "script",
    "static",
    "step",
)


#
# Basic API
#


def static(*paths: str | Iterable[str]):
    """Declare static paths.

    Parameters
    ----------
    *paths
        One or more paths to declare as static, relative to the current working directory.
        Arguments may also be iterables of strings.
        Each string must refer to an existing file or directory and can be one of:

        1. A file: declared immediately as a static path.
        2. A directory: registered as a static tree; files within it are lazily
           declared static the first time they are used as step inputs.

    Raises
    ------
    ValueError
        When a path does not exist, when an environment variable in a path is
        undefined, or when a path contains an invalid variable identifier.

    Notes
    -----
    Environment variables in `paths` are substituted immediately,
    and the variables referenced are added to the calling step's `env_vars` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.
    """
    # Turn paths into one big list.
    _paths = paths
    paths = []
    for path in _paths:
        if isinstance(path, str):
            paths.append(path)
        elif isinstance(path, Iterable):
            paths.extend(path)
    del _paths

    # Avoid empty RPC calls.
    if len(paths) > 0:
        # Perform env var substitutions.
        with subs_env_vars() as subs:
            su_paths = [subs(path).normpath() for path in paths]
        # Sanity checks
        su_file_paths, su_dir_paths = _check_inp_paths(su_paths, allow_dirs=True)
        if len(su_file_paths) > 0:
            # Translate paths to make them relative to the working directory of the director.
            tr_file_paths = sorted(translate(su_file_path) for su_file_path in su_file_paths)
            # Declare the missing and then confirm the files.
            to_check = RPC_CLIENT.call.declare_missing(_get_step_i(), tr_file_paths)
            _confirm_static(to_check)
        if len(su_dir_paths) > 0:
            # Translate paths to make them relative to the working directory of the director.
            tr_dir_paths = sorted(translate(su_dir_path) for su_dir_path in su_dir_paths)
            # Declare the missing and then confirm the directories.
            to_check = RPC_CLIENT.call.static_trees(_get_step_i(), tr_dir_paths)
            _confirm_deferred(to_check)


def glob(*patterns: str, **subs: str) -> NGlobMulti:
    """Declare static files through glob patterns and return the matches.

    All matched files are declared static with the director,
    and the returned object can be iterated in the calling script.

    Parameters
    ----------
    *patterns
        One or more glob patterns relative to the current working directory.
        Patterns may contain anonymous wildcards (`*`, `**`) and named wildcards (`${*name}`).
    **subs
        Override the sub-pattern matched by each named wildcard.
        By default every named wildcard matches `*`.

    Returns
    -------
    ngm
        An `NGlobMulti` instance with all matched paths.
        Iteration yields `NGlobMatch` objects when named wildcards are present,
        or `Path` objects when only anonymous wildcards are used.
        Use `ngm.matches()` or `ngm.files()` to force either mode.
        Use `ngm.single()` to assert and return exactly one matched path.
        Evaluates to `True` in a boolean context when at least one match exists.

    Notes
    -----
    Multiple patterns are matched *jointly*: only combinations of files whose
    named wildcard substitutions are mutually consistent are returned.
    For independent patterns, separate `glob` calls are more efficient.

    Environment variables in `patterns` are substituted before matching,
    and the variables referenced are added to the calling step's `env_vars` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.
    """
    if len(patterns) == 0:
        raise ValueError("At least one path is required for glob.")

    # Substitute environment variables
    with subs_env_vars() as subs_path:
        su_patterns = [subs_path(pattern).normpath() for pattern in patterns]

    # StepUp needs to know the patterns,
    # so it can identify new files matching the patterns in future runs.
    tr_patterns = [translate(su_pattern) for su_pattern in su_patterns]

    # Collect all matches
    nglob_multi = NGlobMulti.from_patterns(su_patterns, subs)
    nglob_multi.glob()

    # Send static paths
    static_paths = nglob_multi.files()
    if len(static_paths) > 0:
        _check_inp_paths(static_paths)
        tr_static_paths = [translate(static_path) for static_path in static_paths]
        to_check = RPC_CLIENT.call.declare_missing(_get_step_i(), tr_static_paths)
        _confirm_static(to_check)

    # Translate all the nglob matches with matching paths and send to the director.
    tr_all_paths = [
        translate(path)
        for nglob_single in nglob_multi.nglob_singles
        for paths in nglob_single.results.values()
        for path in paths
    ]
    RPC_CLIENT.call.nglob(_get_step_i(), tr_patterns, subs, tr_all_paths)

    # Done
    return nglob_multi


def step(
    command: str,
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = ".",
    need: Need = Need.DEFAULT,
    resources: dict[str, int] | str | None = None,
    shell: bool = False,
) -> StepInfo:
    """Add a step to the build graph.

    Parameters
    ----------
    command
        Command to execute (in the given working directory).
    inp
        File(s) required by the step.
        Relative paths are assumed to be relative to `workdir`.
        Directory inputs are not supported.
    env
        Environment variable(s) to which the step is sensitive.
        If they change, or when they are (un)defined, the step digest will change,
        such that the step cannot be skipped.
    out
        File(s) created by the step.
        Relative paths are assumed to be relative to `workdir`.
        Directory outputs are not supported.
    vol
        Volatile file(s) created by the step.
        Relative paths are assumed to be relative to `workdir`.
        Directory outputs are not supported.
    workdir
        The directory where the action must be executed.
        The path is normalized before further processing.
        If this is a relative path, it is relative to the work directory of the caller.
        (The default is the current directory.)
    need
        The level of necessity for the step.
        Three values are allowed:
        - `Need.OPTIONAL` = only execute the step if some of its outputs are (indirectly) needed
          by a non-optional step.
        - `Need.DEFAULT` = execute the step unless the user specifies targets.
        - `Need.PLAN` = always execute the step because it is part of the plan.
    resources
        Named resources required to run this step, e.g. `{"gpu": 1}`.
        One may also provide the resources as a string, e.g. `"gpu:1,memgb:4"`.
        The step will not be scheduled until the required units are available,
        taking into account the units already held by other running steps.
        Resources not listed in `--resources` / `STEPUP_RESOURCES` are treated as unavailable.
        The required units must be strictly positive and default to 1 when not given,
        e.g. `"gpu"` is equivalent to `"gpu:1"`.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Notes
    -----
    Environment variables in `inp`, `out`, `vol`, and `workdir` are substituted immediately,
    and the variables referenced are added to the calling step's `env_vars` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.

    Before sending the step to the director, the variables `${inp}`, `${out}`, and `${vol}`
    in the command are substituted by the space-separated list of `inp`, `out`, and
    `vol`, respectively.
    Relative paths in `inp`, `out`, and `vol` are relative to the working directory of the new step.
    """
    # Pre-process the arguments for the Director process.
    inp_paths = string_to_list(inp)
    env_vars = string_to_list(env)
    out_paths = string_to_list(out)
    vol_paths = string_to_list(vol)
    with subs_env_vars() as subs:
        su_inp_paths = [subs(inp_path).normpath() for inp_path in inp_paths]
        su_out_paths = [subs(out_path).normpath() for out_path in out_paths]
        su_vol_paths = [subs(vol_path).normpath() for vol_path in vol_paths]
        su_workdir = subs(workdir).normpath()
    _check_no_directories(su_inp_paths)
    _check_no_directories(su_out_paths)
    _check_no_directories(su_vol_paths)
    tr_inp_paths = [translate(inp_path, su_workdir) for inp_path in su_inp_paths]
    tr_out_paths = [translate(out_path, su_workdir) for out_path in su_out_paths]
    tr_vol_paths = [translate(vol_path, su_workdir) for vol_path in su_vol_paths]
    tr_workdir = translate(su_workdir)
    # Substitute paths that are translated back to the current directory.
    command = CaseSensitiveTemplate(command).safe_substitute(
        inp=shlex.join(su_inp_paths),
        out=shlex.join(su_out_paths),
        vol=shlex.join(su_vol_paths),
    )

    # Interpret the resources string, if needed.
    if resources is None:
        resources = {}
    elif isinstance(resources, str):
        resources = parse_resources(resources)
    elif not isinstance(resources, dict):
        raise TypeError("The resources argument must be a dict, a string or None.")
    # At this stage, we do not allow non-positive quantities of resources.
    for resource, quantity in resources.items():
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError(
                f"Invalid quantity for resource '{resource}': {quantity}. "
                "Must be a strictly positive integer."
            )

    # Warn when a planning step is registered from a non-planning creator.
    if need == Need.PLAN:
        creator_need_name = os.environ.get("STEPUP_STEP_NEED")
        if creator_need_name is not None and creator_need_name != Need.PLAN.name:
            print(
                f"WARNING: planning step '{command}' is registered from a non-planning step"
                f" (creator need={creator_need_name}). This is likely a workflow authoring error.",
                file=sys.stderr,
            )

    # Finally create the step.
    to_check = RPC_CLIENT.call.step(
        _get_step_i(),
        command,
        tr_inp_paths,
        env_vars,
        tr_out_paths,
        tr_vol_paths,
        tr_workdir,
        need.value,
        resources,
        shell,
    )

    # Check the existence of files matching static trees.
    _confirm_deferred(to_check)

    # Return a StepInfo instance to facilitate the definition of follow-up steps
    return StepInfo(command, tr_workdir, su_inp_paths, env_vars, su_out_paths, su_vol_paths)


def call(
    executable_: str,
    function_: str,
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = ".",
    optional: bool = False,
    planning: bool = False,
    resources: dict[str, int] | str | None = None,
    args_file: str | None = None,
    **kwargs,
) -> StepInfo:
    """Register a step that calls a named function in an executable.

    Parameters
    ----------
    executable_
        Path to the script or binary to invoke.
        Must contain a path separator (e.g. `./script.py` or `sub/script.py`)
        and must not be an absolute path.
        Environment variables in the path are substituted.
    function_
        Name of the function to invoke (first positional CLI argument).
    inp
        Files declared as inputs to this step. Normalized to `list[str]`.
        Also forwarded to the function as `inp`.
    env
        Environment variables tracked by this step.
    out
        Files declared as outputs of this step. Normalized to `list[str]`.
        Also forwarded to the function as `out`.
    vol
        Volatile outputs of this step.
    workdir
        Working directory for the step. Defaults to `"."`.
    optional
        When `True`, the step only runs if its outputs are (indirectly) needed
        by a non-optional step (`Need.OPTIONAL`).
        Mutually exclusive with `planning`.
    planning
        When `True`, the step is scheduled as a planner (`Need.PLAN`).
        Use this when the called function registers further steps.
        Mutually exclusive with `optional`.
    resources
        Resource constraints for this step.
    args_file
        Full filename for the serialized arguments.
        When given, arguments are written to this file (format inferred from extension)
        and passed via `--inp=<args_file>`; when absent, a JSON string is embedded
        directly in the command.
    **kwargs
        Additional keyword arguments forwarded to the function.
        Must be serializable to JSON by the `cattrs` JSON converter.

    Returns
    -------
    step_info
        Holds relevant information of the registered step.

    Raises
    ------
    ValueError
        When `optional` and `planning` are both `True`.
    ValueError
        When `executable_` does not contain a path separator or is absolute.
    ValueError
        When the inline JSON string exceeds 128 KiB (use `args_file` instead).
    ValueError
        When `args_file` has an unrecognized extension.
    """
    # Validate mutually exclusive flags.
    if optional and planning:
        raise ValueError("optional and planning are mutually exclusive")

    # Perform environment variable substitutions before building the command.
    # This is somewhat redundant with the substitutions performed in `_prepare_run_command()`.
    with subs_env_vars() as subs:
        executable_ = subs(executable_)
        inp = [subs(inp_path).normpath() for inp_path in string_to_list(inp)]
        out = [subs(out_path).normpath() for out_path in string_to_list(out)]
        workdir = subs(workdir).normpath()
    prefix, suffix = get_affixes(executable_)
    executable_ = apply_affixes(executable_.normpath(), prefix, suffix)

    # Validate executable path format.
    if os.sep not in executable_:
        raise ValueError(
            f"executable_ must contain a path separator (e.g. './script.py'), got: {executable_!r}"
        )

    # Validate the executable is not absolute.
    if os.path.isabs(executable_):
        raise ValueError(f"executable_ must not be an absolute path, got: {executable_!r}")

    # Build the forwarded kwargs dict (inp and out are included when not empty).
    forwarded = kwargs.copy()
    if len(inp) > 0:
        forwarded["inp"] = inp
    if len(out) > 0:
        forwarded["out"] = out

    # Build command and step inputs depending on args_file mode.
    if args_file is None:
        unstructured = json_converter.unstructure(forwarded)
        json_str = json.dumps(unstructured)
        if len(json_str.encode()) > 128 * 1024:
            raise ValueError(
                "serialized call arguments exceed 128 KiB; pass args_file= to use a file instead"
            )
        command = f"{shlex.quote(executable_)} {function_} {shlex.quote(json_str)}"
        step_inp = [executable_, *inp]
    else:
        # dumpns(do_amend=True) calls amend(out=args_file) before writing.
        dumpns(args_file, forwarded)
        command = f"{shlex.quote(executable_)} {function_} --inp={shlex.quote(args_file)}"
        step_inp = [executable_, *inp, args_file]

    # Map optional/planning flags to Need enum.
    if optional:
        need = Need.OPTIONAL
    elif planning:
        need = Need.PLAN
    else:
        need = Need.DEFAULT

    # Register and return the step.
    return step(
        command,
        inp=step_inp,
        out=out,
        vol=vol,
        env=env,
        workdir=workdir,
        need=need,
        resources=resources,
    )


class InputNotFoundError(Exception):
    """Raised when amended inputs are not available yet."""


# A history used to avoid amending the same information twice.
# This effectively reduces the amount of amend API calls.
AMEND_HISTORY = {
    "inp": set(),
    "env": set(),
    "out": set(),
    "vol": set(),
}


def amend(
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
):
    """Declare additional inputs, outputs, and environment dependencies from within a running step.

    Parameters
    ----------
    inp
        Files required by the step.
        Relative paths are relative to the step's working directory.
        Directory inputs are not supported.
    env
        Environment variables to which the step is sensitive.
        If they change, or when they are (un)defined, the step digest will change,
        such that the step cannot be skipped.
    out
        Files created by the step.
        Relative paths are relative to the step's working directory.
        Directory outputs are not supported.
    vol
        Volatile files created by the step.
        Relative paths are relative to the step's working directory.
        Directory outputs are not supported.

    Raises
    ------
    InputNotFoundError
        When amended inputs are not yet available.
        Let this exception propagate — do not catch it.
        The director reschedules the step once the missing inputs become available.

    Notes
    -----
    Environment variables in `inp`, `out`, and `vol` are substituted immediately,
    and the variables referenced are added to the calling step's `env_vars` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.

    Always call `amend()` before reading input files and before writing output or volatile files.

    Repeated calls are safe: items already amended in prior calls are silently skipped.
    """
    # Pre-process the arguments for the Director process.
    inp_paths = string_to_list(inp)
    env_vars = string_to_list(env)
    out_paths = string_to_list(out)
    vol_paths = string_to_list(vol)
    if all(len(collection) == 0 for collection in [inp_paths, env_vars, out_paths, vol_paths]):
        return
    env_vars = set(env_vars)
    with subs_env_vars() as subs:
        su_inp_paths = {subs(inp_path).normpath() for inp_path in inp_paths}
        tr_inp_paths = {translate(inp_path) for inp_path in su_inp_paths}
        tr_out_paths = {translate(subs(out_path)) for out_path in out_paths}
        tr_vol_paths = {translate(subs(vol_path)) for vol_path in vol_paths}
    _check_no_directories(tr_inp_paths)
    _check_no_directories(tr_out_paths)
    _check_no_directories(tr_vol_paths)

    # Filter out previously amended information
    tr_inp_paths.difference_update(AMEND_HISTORY["inp"])
    env_vars.difference_update(AMEND_HISTORY["env"])
    tr_out_paths.difference_update(AMEND_HISTORY["out"])
    tr_vol_paths.difference_update(AMEND_HISTORY["vol"])

    if (
        len(tr_inp_paths) == 0
        and len(env_vars) == 0
        and len(tr_out_paths) == 0
        and len(tr_vol_paths) == 0
    ):
        return

    # Finally, amend for real.
    step_i = _get_step_i()
    amend_result = RPC_CLIENT.call.amend(
        step_i,
        tr_inp_paths,
        sorted(env_vars),
        tr_out_paths,
        tr_vol_paths,
    )
    if amend_result is not None:
        keep_going, to_check = amend_result
        if keep_going is False:
            raise InputNotFoundError("Amended inputs are not available yet.")
        _confirm_deferred(to_check, step_i)

    # Double check that all inputs are indeed present.
    _check_inp_paths(su_inp_paths)

    # Update the amendment history
    AMEND_HISTORY["inp"].update(tr_inp_paths)
    AMEND_HISTORY["env"].update(env_vars)
    AMEND_HISTORY["out"].update(tr_out_paths)
    AMEND_HISTORY["vol"].update(tr_vol_paths)


def getinfo() -> StepInfo:
    """Get the information of the current step.

    Returns
    -------
    step_info
        Holds relevant information of the current step, useful for defining follow-up steps.
        For consistency with other functions in this module, the `inp`, `out` and `vol`
        paths are relative to the working directory of the step.
    """
    step_info = RPC_CLIENT.call.getinfo(_get_step_i())
    # Update paths to make them relative to the working directory of the step.
    step_info.inp = sorted(translate_back(inp) for inp in step_info.inp)
    step_info.out = sorted(translate_back(out) for out in step_info.out)
    step_info.vol = sorted(translate_back(vol) for vol in step_info.vol)
    return step_info


def graph(prefix: str):
    """Write the workflow graph files in text and dot formats."""
    return RPC_CLIENT.call.graph(prefix)


#
# Composite functions, created with the functions above.
#


def _prepare_run_command(
    command: str, inp: Collection[str] | str, shell: bool, need_relative_exe: bool
) -> tuple[str, list[str]]:
    """Prepare command for execution and auto-add local executable as input.

    Parameters
    ----------
    command
        The command to execute, optionally followed by arguments.
    inp
        The user-specified input files.
    shell
        Whether to execute the command in a shell (passed through unchanged).
    need_relative_exe
        Whether to require the command to be a relative path to a local executable.
        If True, the command must contain at least one slash and cannot be an absolute path.

    Returns
    -------
    command
        The command with `${inp}` substituted by the user-specified inputs,
        excluding the auto-added executable if applicable.
    inp
        The updated list of input files, including the auto-added executable if applicable.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if len(parts) == 0:
        raise ValueError("The command must not be empty.")
    # Pre-substitute ${inp} with only the user-specified inputs.
    # This prevents the auto-added executable from appearing in the ${inp} expansion.
    # For example, this avoids duplicating the script in commands like `./script.sh ${inp}`.
    inp = string_to_list(inp)
    with subs_env_vars() as subs:
        su_inp = [subs(path_inp).normpath() for path_inp in inp]
    command = CaseSensitiveTemplate(command).safe_substitute(inp=shlex.join(su_inp))
    # Auto-add the executable to inp when it is a local relative path
    # (contains "/" but is not absolute).
    exe = parts[0]
    if os.sep in exe and not exe.startswith(os.sep):
        inp = [exe, *inp]
    elif need_relative_exe:
        raise ValueError(
            "The command must be a relative path to a local executable, "
            f"containing at least one slash, e.g. './plan.py'. Got: {command}"
        )
    return command, inp


def run(
    command: str,
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = ".",
    optional: bool = False,
    shell: bool = False,
    resources: dict[str, int] | str | None = None,
) -> StepInfo:
    """Add a command to the build graph.

    Parameters
    ----------
    command
        The command to execute, optionally followed by arguments.
        The execution method is selected automatically at run time:

        - If `shell=True`: the command is passed to a shell.
          Shell features like pipes and redirections are supported.
        - If `shell=False` and the first word ends in `.py`:
          the script is executed via a Python wrapper
          that auto-detects local imports.
          Shell features are not available in this mode.
        - If `shell=False` and the first word is a bare command name (no slashes) that
          matches a `console_scripts` entry point in the current Python environment:
          the entry point is called in-process via the forkserver when available,
          avoiding subprocess overhead.
          If the entry point belongs to a different Python environment, a warning is
          logged and the command falls back to direct subprocess execution.
        - Otherwise: the command is executed directly without a shell.
          This is faster and safer than the shell mode.

        When the first word contains a `/` and is not an absolute path (e.g. `./script.py`,
        `subdir/tool`), it is automatically added as an input dependency.
        Bare command names like `echo` or absolute paths like `/usr/bin/gcc` are not added.

        Python detection uses the `.py` file extension only,
        so it works even when the script does not yet exist (e.g. it is an output of another step).
        `shell=True` takes precedence and disables Python auto-detection.
    inp, env, out, vol, workdir, optional, resources
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    command, inp = _prepare_run_command(command, inp, shell, need_relative_exe=False)
    return step(
        command,
        inp=inp,
        env=env,
        out=out,
        vol=vol,
        workdir=workdir,
        need=Need.OPTIONAL if optional else Need.DEFAULT,
        resources=resources,
        shell=shell,
    )


def plan(
    command: str,
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = ".",
    resources: dict[str, int] | str | None = None,
) -> StepInfo:
    """Run a planning script.

    The main difference with [`run()`][stepup.core.api.run] is that the step is flagged
    as planner internally, which will give it higher priority than non-planner steps.
    This results in earlier knowledge of the workflow, which improves scheduling efficiency.

    Compared to the `run()` function, this function imposes `optional=False` and `shell=False`.

    Parameters
    ----------
    command
        The command to execute, optionally followed by arguments.
        The execution method is selected automatically at run time:

        - If the first word ends in `.py`:
          the script is executed via a Python wrapper
          that auto-detects local imports.
        - Otherwise the command is executed directly without a shell.
          This scenario is highly unlikely but supported just for completeness.

        Bare command names like `echo` or absolute paths like `/usr/bin/gcc` are not allowed.
        The command must always be a relative path to a local executable script.
    inp, env, out, vol, workdir, resources
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    command, inp = _prepare_run_command(command, inp, shell=False, need_relative_exe=True)
    # Note that we do not use `run()` here because we need to set `need=Need.PLAN`.
    return step(
        command,
        inp=inp,
        env=env,
        out=out,
        vol=vol,
        workdir=workdir,
        need=Need.PLAN,
        resources=resources,
        shell=False,
    )


def copy(
    src: str,
    dst: str,
    *,
    optional: bool = False,
    resources: dict[str, int] | str | None = None,
) -> StepInfo:
    """Add a step that copies a file.

    Parameters
    ----------
    src
        This must be a file. Environment variables are substituted.
    dst
        This can be a file or a directory. Environment variables are substituted.
        If `dst` denotes a directory, it must have a trailing slash
        and `src` will be copied inside it with its original name.
    optional, resources
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Notes
    -----
    Environment variables in `src` and `dst` are substituted immediately,
    and the variables referenced are added to the calling step's `env_vars` list with `amend()`.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the copy is actually made.
    """
    with subs_env_vars() as subs:
        src = subs(src).normpath()
        dst = subs(dst)
    prefix, suffix = get_affixes(dst)
    dst = apply_affixes(dst.normpath(), prefix, suffix)
    dst = make_path_out(src, dst, None)
    return step(
        "cp -p ${inp} ${out}",
        inp=src,
        out=dst,
        need=Need.OPTIONAL if optional else Need.DEFAULT,
        resources=resources,
        shell=False,
    )


def getenv(
    name: str,
    default: str | None = None,
    *,
    path: bool = False,
    back: bool = False,
    multi: bool = False,
) -> str | Path | list[Path]:
    """Get an environment variable and amend the current step with the variable name.

    Parameters
    ----------
    name
        The name of the environment variable, which is retrieved with `os.getenv`.
    default
        The value to return when the environment variable is unset.
    path
        Set to True if the variable taken from the environment is assumed to be a path.
        A Path instance will be returned.
        Shell variables are substituted (once) in such paths.
    back
        Set to True to translate the path back to the working directory of the caller.
        If the path is relative, it is assumed to be relative to the StepUp's working directory.
        It will be translated to become relative to the working directory of the caller.
        This implies `path=True`.
    multi
        Set to True if the variable is a list of paths.
        The paths are split on the colon character and returned as a list of `Path` instances.
        This implies `path=True`.

    Returns
    -------
    value
        The value of the environment variable.
        If `path` is set to `True`, this is a `Path` instance.
        If `back` is set to `True`, this is a translated `Path` instance.
        If `multi` is set to `True`, this is a list of `Path` instances.
        Otherwise, the result is a string.
        All path variables are normalized.
    """
    path = path or back or multi
    value = os.getenv(name, default)
    # Do not amend environment variables set for the step by the executor.
    # See stepup.core.executor.StepExecutor.run
    if name not in ["HERE", "ROOT", "STEPUP_STEP_I", "STEPUP_STEP_INP_DIGEST", "STEPUP_STEP_NEED"]:
        amend(env=name)

    if multi:
        if value is None:
            return []
        parts = value.split(":")
        value = []
        with subs_env_vars() as subs:
            for item in parts:
                item = item.strip()
                if len(item) > 0:
                    item = subs(item)
                    prefix, suffix = get_affixes(item)
                    item = item.normpath()
                    if back:
                        item = translate_back(item)
                    value.append(apply_affixes(item, prefix, suffix))
    elif path:
        if value is None:
            raise ValueError(f"Undefined shell variable: {name}. Cannot create path.")
        with subs_env_vars() as subs:
            value = subs(value)
        prefix, suffix = get_affixes(value)
        value = value.normpath()
        if back:
            value = translate_back(value)
        value = apply_affixes(value, prefix, suffix)
    return value


def script(
    executable: str,
    *,
    step_info: str | None = None,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = ".",
    optional: bool = False,
    resources: dict[str, int] | str | None = None,
) -> StepInfo:
    """Run the executable with a single argument `plan` in a working directory.

    !!! warning

        The script interface for calling user Python scripts from `plan.py` has been deprecated
        in favor of the new [Call](../getting_started/call.md) interface.
        You are encouraged to migrate your `plan.py` files to the new API.
        See [the migration guide][sc] for a step-by-step walkthrough.

        [sc]: ../migration/from_3x_to_40.md#optional-migration-from-script-to-call

    Parameters
    ----------
    executable
        The path of a local executable that will be called with the argument `plan`.
        The file must be executable.
        The path of the script is assumed to be relative to this directory.
        Environment variables in the path are substituted.
    step_info
        When given, the steps generated in the plan part of the executable are written
        to this `step_info` file. (See [stepup.core.stepinfo][] module for the file format.)
        This filename is relative to the work directory.
    inp, env, out, vol, workdir, optional, resources
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Notes
    -----
    - The arguments `inp`, `env`, `out` and `vol` are rarely needed for script steps.
      They only apply to the plan stage of the script, not the run stage.
    - The `inp` argument may be useful when the planning is configured by some input files.
    - The optional argument never applies to the plan stage,
      and is passed on the the run stage.
    """
    # Substitute environment variables in the executable path and normalize it.
    with subs_env_vars() as subs:
        executable = subs(executable)
    prefix, suffix = get_affixes(executable)
    executable = apply_affixes(executable.normpath(), prefix, suffix)

    # Start building the command and the step inputs.
    command = format_command(executable) + " plan"
    out = string_to_list(out)
    if step_info is not None:
        command += " --step-info=" + shlex.quote(step_info)
        out.append(step_info)
    if optional:
        command += " --optional"
    inp = string_to_list(inp)
    inp.append(executable)
    step_kwargs = {
        "inp": inp,
        "env": env,
        "out": out,
        "vol": vol,
        "workdir": workdir,
        "need": Need.PLAN,
        "resources": resources,
    }
    # Note that we do not use `run()` here because we need to set `need=Need.PLAN`.
    return step(command, **step_kwargs)


def loadns(
    *paths_variables: str, dir_out: str | None = None, do_amend: bool = True
) -> SimpleNamespace:
    """Load variable from Python, JSON, TOML or YAML files and put them in a namespace.

    Parameters
    ----------
    paths_variables
        paths of Python, JSON, TOML or YAML files containing variable definitions.
        They are loaded in the given order, so later variable definitions may overrule earlier ones.
        Environment variables in path names are substituted.
    dir_out
        This is used to translate paths defined in the variables files
        (relative to parent of the variable file)
        to paths relative to `dir_out`.
        If not given, the current working directory is used.
        This is only relevant for variables loaded from Python files.
    do_amend
        If ``True``, All loaded files are amended as inputs to the current step.

    Returns
    -------
    variables
        A SimpleNamespace instance with the variables, which can be accessed as attributes.
    """
    # Process arguments
    if dir_out is None:
        dir_out = Path.cwd()
    with subs_env_vars() as subs:
        paths_variables = [subs(path_var).normpath() for path_var in paths_variables]

    # Build a dictionary of variables
    variables = {}
    for path_var in paths_variables:
        path_var = Path(path_var)
        if path_var.suffix == ".json":
            with open(path_var) as fh:
                variables.update(json.load(fh))
        elif path_var.suffix == ".toml":
            with open(path_var, "rb") as fh:
                variables.update(tomllib.load(fh))
        elif path_var.suffix in (".yaml", ".yml"):
            with open(path_var) as fh:
                variables.update(yaml.safe_load(fh))
        elif path_var.suffix == ".py":
            dir_py = path_var.parent.normpath()
            fn_py = path_var.name
            with contextlib.chdir(dir_py):
                sys.path.insert(0, str(dir_py))
                try:
                    current = run_path(fn_py, run_name="<variables>")
                finally:
                    sys.path.remove(dir_py)
            for name, value in current.items():
                if name.startswith("_"):
                    continue
                if isinstance(value, Path):
                    value = Path(value).relpath(dir_out)
                variables[name] = value
        else:
            raise ValueError(f"unsupported variable file format: {path_var}")
    if do_amend:
        amend(inp=paths_variables)

    # Return as a namespace
    return SimpleNamespace(**variables)


def dumpns(path: str, data: dict | SimpleNamespace, *, do_amend: bool = True) -> None:
    """Write variables to a JSON or YAML file.

    Parameters
    ----------
    path
        Destination file path. The format is inferred from the extension:
        `.json` for JSON, `.yaml` or `.yml` for YAML.
        Environment variables in the path are substituted.
    data
        A `dict` or `SimpleNamespace` of variables to write.
        `cattrs`-supported types (attrs classes, dataclasses) are unstructured automatically.
    do_amend
        If `True`, the file is amended as an output of the current step before writing.

    Raises
    ------
    ValueError
        When the file extension is not `.json`, `.yaml`, or `.yml`.
    """
    with subs_env_vars() as subs:
        path = subs(path).normpath()
    if do_amend:
        amend(out=path)
    if isinstance(data, SimpleNamespace):
        data = vars(data)
    path_obj = Path(path)
    if path_obj.suffix == ".json":
        unstructured = json_converter.unstructure(data)
        with open(path_obj, "w") as fh:
            json.dump(unstructured, fh, indent=2)
            fh.write("\n")
    elif path_obj.suffix in (".yaml", ".yml"):
        unstructured = yaml_converter.unstructure(data)
        with open(path_obj, "w") as fh:
            yaml.dump(unstructured, fh)
    else:
        raise ValueError(f"dumpns: unsupported file format: {path_obj.suffix!r}")


def render_jinja(
    *args: str | dict,
    mode: str = "auto",
    optional: bool = False,
    resources: dict[str, int] | str | None = None,
) -> StepInfo:
    """Render the template with Jinja2.

    Parameters
    ----------
    args
        The first argument is the path to the template file.
        All the following position arguments can be one of the following two types:

        - Paths to Python, JSON, TOML or YAML files with variable definitions.
          Variables defined in later files take precedence.
        - A dictionary with additional variables.
          These will be JSON-serialized and passed on the command-line to the Jinja renderer.
          Variables in dictionaries take precedence over variables from files.
          When multiple dictionaries are given, later ones take precedence.

        The very last argument is an output destination (directory or file).
    mode
        The format of the Jinja placeholders:

        - The default (auto) selects either `plain` or `latex`,
          based on the extension of the output file.
        - The `plain` format is the default Jinja style with curly brackets: `{{ }}` etc.
        - The `latex` style replaces curly brackets by angle brackets: `<< >>` etc.
    optional, resources
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Notes
    -----
    At least some variables must be given, either as a file containing variables or as a dictionary.
    """
    # Parse the positional arguments
    if len(args) < 3:
        raise ValueError(
            "At least three positional arguments must be given: "
            "the template, at least one file or dict with variables, and the destination."
        )
    path_template = args[0]
    if not isinstance(path_template, str):
        raise TypeError("The template argument must be a string.")
    dest = args[-1]
    if not isinstance(dest, str):
        raise TypeError("The destination argument must be a string.")
    variables = {}
    paths_variables = []
    for arg in args[1:-1]:
        if isinstance(arg, str):
            paths_variables.append(arg)
        elif isinstance(arg, dict):
            variables.update(arg)
        else:
            raise TypeError("The variables arguments must be strings (paths) or dictionaries.")

    # Parse other arguments.
    if mode not in ["auto", "plain", "latex"]:
        raise ValueError(f"Unsupported mode {mode!r}. Must be one of 'auto', 'plain', 'latex'")
    if len(paths_variables) == 0 and len(variables) == 0:
        raise ValueError("At least one file with variable definitions needed.")
    path_out = make_path_out(path_template, dest, None)

    # Create the command
    args = ["stepup", "render-jinja", "${inp}", "${out}"]
    if mode != "auto":
        args.append(f"--mode={mode}")
    if len(variables) > 0:
        args.append("--json=" + shlex.quote(json.dumps(variables)))
    return step(
        " ".join(args),
        inp=[path_template, *paths_variables],
        out=path_out,
        need=Need.OPTIONAL if optional else Need.DEFAULT,
        resources=resources,
    )


#
# Internal stuff
#


class DeferredNotConfirmedError(Exception):
    """Raised when static tree matches cannot be confirmed."""


def _confirm_static(to_check: list[tuple[str, FileHash]] | None):
    """Confirm initially missing files and send the updates to the director."""
    # When the RPC_CLIENT is a dummy, to_check may be `None`.
    if to_check is not None and len(to_check) > 0:
        checked = []
        for tr_path, old_file_hash in to_check:
            new_file_hash = old_file_hash.regen(translate_back(tr_path))
            if new_file_hash != old_file_hash:
                checked.append((tr_path, new_file_hash))
        if len(checked) > 0:
            RPC_CLIENT.call.confirm_hashes(checked)


def _confirm_deferred(to_check: list[tuple[str, FileHash]] | None, step_i: int | None = None):
    """Check file, update hashes of existing ones, and send the updates to the director."""
    if to_check is not None and len(to_check) > 0:
        # Select matches of the static tree that exist and update their hashes.
        checked = []
        missing = []
        for tr_path, old_file_hash in to_check:
            new_file_hash = old_file_hash.regen(translate_back(tr_path))
            if new_file_hash != old_file_hash:
                checked.append((tr_path, new_file_hash))
            if new_file_hash.is_unknown:
                missing.append(tr_path)
        if len(checked) > 0:
            RPC_CLIENT.call.confirm_hashes(checked)
        if len(missing) > 0:
            message = "\n".join(missing)
            if step_i is not None:
                RPC_CLIENT.call.reschedule_step(step_i, message)
            raise DeferredNotConfirmedError(message)


def _check_inp_paths(
    inp_paths: Iterable[Path], allow_dirs: bool = False
) -> tuple[list[Path], list[Path]]:
    """Check the validity of the input paths."""
    file_paths = []
    dir_paths = []
    for inp_path in inp_paths:
        is_dir = inp_path.is_dir()
        if is_dir:
            dir_paths.append(inp_path)
        else:
            file_paths.append(inp_path)
        if not allow_dirs:
            if inp_path.endswith(os.sep):
                raise ValueError(f"Directory inputs are not supported: {inp_path}")
            if is_dir:
                raise ValueError(f"Directory inputs are not supported: {inp_path}")
        if not inp_path.exists():
            raise ValueError(f"Path does not exist: {inp_path}")
    return file_paths, dir_paths


def _check_no_directories(paths: Iterable[Path]):
    """Check that the paths are not directories."""
    for path in paths:
        if path.endswith(os.sep):
            raise ValueError(f"Directories are not allowed: {path}")


def get_rpc_client(socket: str | None = None):
    """Try setting up a Synchronous RPC client or fall back to the dummy client if that fails."""
    stepup_director_socket = os.getenv("STEPUP_DIRECTOR_SOCKET", socket)
    if stepup_director_socket == "_invalid_socket_for_director_process_":
        raise RuntimeError("The RPC client is being used within the director process.")
    if stepup_director_socket is None:
        return DummySyncRPCClient()
    return SocketSyncRPCClient(stepup_director_socket)


RPC_CLIENT = get_rpc_client()


def _get_step_i() -> int:
    """Get the current step node index from the STEPUP_STEP_I environment variable."""
    step_i = os.getenv("STEPUP_STEP_I")
    if step_i is None:
        if not isinstance(RPC_CLIENT, SocketSyncRPCClient):
            return -1
        raise RuntimeError("The STEPUP_STEP_I environment variable is not defined.")
    return int(step_i)
