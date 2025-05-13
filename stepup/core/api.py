# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
- inside the driver functions in `stepup.core.call` and `stepup.core.script`
"""

import argparse
import contextlib
import json
import os
import pickle
import shlex
import sys
import tomllib
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from runpy import run_path

import yaml
from path import Path

from .nglob import NGlobMulti
from .rpc import DummySyncRPCClient, SocketSyncRPCClient
from .step import FileHash
from .stepinfo import StepInfo
from .utils import (
    CaseSensitiveTemplate,
    check_inp_path,
    format_command,
    make_path_out,
    mynormpath,
    myparent,
    string_to_list,
    translate,
    translate_back,
)

__all__ = (
    "RPC_CLIENT",
    "amend",
    "call",
    "copy",
    "get_rpc_client",
    "getenv",
    "getinfo",
    "glob",
    "graph",
    "loadns",
    "mkdir",
    "plan",
    "pool",
    "render_jinja",
    "runsh",
    "script",
    "static",
    "step",
    "subs_env_vars",
)


#
# Basic API
#


def static(*paths: str | Iterable[str]):
    """Declare static paths.

    Parameters
    ----------
    *paths
        One or more static paths (files or directories),
        relative to the current working directory.
        Arguments may also be lists of strings.

    Raises
    ------
    ValueError
        When a file does not exist or there is an error with the trailing separator.

    Notes
    -----
    Environment variables in the `paths` will be
    substituted directly and amend the current step's env_vars list, if needed.
    These substitutions will ignore changes to `os.environ` made in the calling script.
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
            su_paths = [subs(path) for path in paths]
        # Sanity checks
        _check_inp_paths(su_paths)
        # Translate paths to make them relative to the working directory of the director.
        tr_paths = sorted(translate(su_path) for su_path in su_paths)
        # Declare the missing and then confirm the files.
        to_check = RPC_CLIENT.call.missing(_get_step_i(), tr_paths)
        _confirm_missing(to_check)


def glob(
    *patterns: str, _required: bool = False, _defer: bool = False, **subs: str
) -> NGlobMulti | None:
    """Declare static paths through pattern matching.

    Parameters
    ----------
    *patterns
        One or more patterns for static files or directories,
        relative to the current working directory.
        The patterns may contain (named) wildcards and one
        may specify the pattern for each named wildcard with
        the keyword arguments.
    _required
        When True, an error will be raised when there are no matches.
    _defer
        When True, static files are not added yet.
        Instead, the glob is installed in the workflow as a deferred glob.
        As soon as any file is needed as input and matches the pattern,
        it will be made static.
        This is not compatible with `_required=True`.
        Named wildcards are not supported in deferred globs.
    **subs
        When using named wildcards, they will match the pattern `*` by default.
        Through the subs argument each name can be associated with another glob pattern.
        Names starting with underscores are not allowed.

    Raises
    ------
    FileNotFoundError
        when no matches were found and _required is True.

    Returns
    -------
    ngm
        An `NGlobMulti` instance holding all the matched (combinations of) paths.
        This object acts as an iterator.
        When named wildcards are used, it iterates over `NGlobMatch` instances.
        When using only anonymous wildcards, it iterates over unique paths.
        It also features `ngm.matches()` and `ngm.files()` iterators,
        with which the type of iterator can be overruled.
        Finally, one may also use ngm in conditional expressions:
        It evaluates to True if and only if it contains some matches.

        `None` is returned when `_defer=True`.

    Notes
    -----
    The combinatorics allow one to construct nested loops easily in one call.
    For unrelated patterns, it may be more efficient to use separate `glob` calls.

    Environment variables in the `patterns` will be
    substituted directly and amend the current step's env_vars list, if needed.
    These substitutions will ignore changes to `os.environ` made in the calling script.
    """
    if len(patterns) == 0:
        raise ValueError("At least one path is required for glob.")
    if any(name.startswith("_") for name in subs):
        raise ValueError("Substitutions cannot have names starting with underscores.")

    # Substitute environment variables
    with subs_env_vars() as subs_path:
        su_patterns = [subs_path(pattern) for pattern in patterns]

    tr_patterns = [translate(su_pattern) for su_pattern in su_patterns]
    if _defer:
        if _required:
            raise ValueError("Combination of options not supported: _defer=True, _required=True")
        if len(subs) > 0:
            raise ValueError("Named wildcards are not supported in deferred globs.")
        to_check = RPC_CLIENT.call.defer(_get_step_i(), tr_patterns)
        _check_deferred(to_check)
        return None

    # Collect all matches
    nglob_multi = NGlobMulti.from_patterns(su_patterns, subs)
    nglob_multi.glob()
    if _required and len(nglob_multi.results) == 0:
        raise FileNotFoundError("Could not find any matching paths on the filesystem.")

    # Send static paths
    static_paths = nglob_multi.files()
    if len(static_paths) > 0:
        _check_inp_paths(static_paths)
        tr_static_paths = [translate(static_path) for static_path in static_paths]
        to_check = RPC_CLIENT.call.missing(_get_step_i(), tr_static_paths)
        _confirm_missing(to_check)

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
    action: str,
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = "./",
    optional: bool = False,
    pool: str | None = None,
    block: bool = False,
) -> StepInfo:
    """Add a step to the build graph.

    Parameters
    ----------
    action
        Action to execute (in the given working directory).
    inp
        File(s) required by the step.
        Relative paths are assumed to be relative to `workdir`.
        Can be files or directories (trailing slash).
    env
        Environment variable(s) to which the step is sensitive.
        If they change, or when they are (un)defined, the step digest will change,
        such that the step cannot be skipped.
    out
        File(s) created by the step.
        Relative paths are assumed to be relative to `workdir`.
        These can be files or directories (trailing slash).
    vol
        Volatile file(s) created by the step
        Relative paths are assumed to be relative to `workdir`.
        Directories are not allowed.
    workdir
        The directory where the action must be executed.
        A trailing slash is added when not present.
        If this is a relative path, it is relative to the work directory of the caller.
        (The default is the current directory.)
    optional
        When set to True, the step is only executed when required by other mandatory steps.
    pool
        If given, the execution of this step is restricted to the pool with the given name.
        The maximum number of parallel steps running in this pool is determined by the pool size.
    block
        When set to True, the step will always remain pending.
        This can be used to temporarily prevent part of the workflow from being executed.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Notes
    -----
    Environment variables in the `workdir`, `inp`, `out` and `vol` paths and workdir will be
    substituted directly and amend the current step's env_vars list, if needed.
    These substitutions will ignore changes to `os.environ` made in the calling script.

    Before sending the step to the director the variables `${inp}`, `${out}` and `${vol}`
    in the action are substituted by white-space concatenated list of `inp`, `out` and
    `vol`, respectively.
    Relative paths in `inp`, `out` and `vol` are relative to the working directory of the new step.
    """
    # Pre-process the arguments for the Director process.
    inp_paths = string_to_list(inp)
    env_vars = string_to_list(env)
    out_paths = string_to_list(out)
    vol_paths = string_to_list(vol)
    if not workdir.endswith("/"):
        workdir = f"{workdir}/"
    amended_env_vars = set()
    with subs_env_vars() as subs:
        su_inp_paths = [subs(inp_path) for inp_path in inp_paths]
        su_out_paths = [subs(out_path) for out_path in out_paths]
        su_vol_paths = [subs(vol_path) for vol_path in vol_paths]
        su_workdir = subs(workdir)
    amend(env=sorted(amended_env_vars))
    tr_inp_paths = [translate(inp_path, su_workdir) for inp_path in su_inp_paths]
    tr_out_paths = [translate(out_path, su_workdir) for out_path in su_out_paths]
    tr_vol_paths = [translate(vol_path, su_workdir) for vol_path in su_vol_paths]
    tr_workdir = translate(su_workdir)
    # Substitute paths that are translated back to the current directory.
    action = CaseSensitiveTemplate(action).safe_substitute(
        inp=shlex.join(su_inp_paths),
        out=shlex.join(su_out_paths),
        vol=shlex.join(su_vol_paths),
    )

    # Finally create the step.
    to_check = RPC_CLIENT.call.step(
        _get_step_i(),
        action,
        tr_inp_paths,
        env_vars,
        tr_out_paths,
        tr_vol_paths,
        tr_workdir,
        optional,
        pool,
        block,
    )

    # Check the existence of files matching deferred globs.
    _check_deferred(to_check)

    # Return a StepInfo instance to facilitate the definition of follow-up steps
    return StepInfo(action, tr_workdir, su_inp_paths, env_vars, su_out_paths, su_vol_paths)


def pool(name: str, size: int):
    """Define a pool with given size or change an existing pool size.

    Parameters
    ----------
    name
        The name of the pool.
    size
        The pool size.
    """
    RPC_CLIENT.call.pool(_get_step_i(), name, size)


class InputNotFoundError(Exception):
    """Raised when amended inputs are not available yet."""


def amend(
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
):
    """Specify additional inputs and outputs from within a running step.

    Parameters
    ----------
    inp
        Files required by the step.
        Can be files or directories (trailing slash).
    env
        Environment variables to which the step is sensitive.
        If the change, or when they are (un)defined, the step digest will change,
        such that the step is not skipped when these variables change.
    out
        Files created by the step.
        Can be files or directories (trailing slash).
    vol
        Volatile files created by the step.
        Can be files or directories (trailing slash).

    Raises
    ------
    InputNotFoundError
        When amended inputs are not available yet.
        There is no need to catch this exception.
        Instead, just let it fail the calling script,
        so that it can be rescheduled for later execution.
        The director has been informed that some of the amended inputs were not available yet.

    Notes
    -----
    Environment variables in the `inp`, `out` and `vol` paths are substituted in the same way
    as in the `step()` function. The used variables are added to the env_vars argument.

    Always call amend before using the input files
    and before creating the output and volatile files.
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
        su_inp_paths = [subs(inp_path) for inp_path in inp_paths]
        tr_inp_paths = [translate(inp_path) for inp_path in su_inp_paths]
        tr_out_paths = [translate(subs(out_path)) for out_path in out_paths]
        tr_vol_paths = [translate(subs(vol_path)) for vol_path in vol_paths]

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
        _check_deferred(to_check, step_i)
    # Double check that all inputs are indeed present.
    _check_inp_paths(su_inp_paths)


def getinfo() -> StepInfo:
    """Get the information of the current step.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
        For consistency with other functions in this module, the `inp`, `out` and `vol`
        paths are relative to the working directory of the step.
    """
    step_info = RPC_CLIENT.call.getinfo(_get_step_i())
    # Update paths to make them relative to the working directory of the step.
    step_info.inp = sorted(translate_back(inp) for inp in step_info.inp)
    step_info.out = sorted(translate_back(out) for out in step_info.out)
    step_info.vol = sorted(translate_back(vol) for vol in step_info.vol)
    # Filter required directorie out of the inputs.
    reqdirs = {myparent(path) for path in step_info.out}
    reqdirs.update({myparent(path) for path in step_info.vol})
    step_info.inp = [path for path in step_info.inp if path not in reqdirs]
    return step_info


def graph(prefix: str):
    """Write the workflow graph files in text and dot formats."""
    return RPC_CLIENT.call.graph(prefix)


#
# Composite functions, created with the functions above.
#


def runsh(
    command: str,
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = "./",
    optional: bool = False,
    pool: str | None = None,
    block: bool = False,
) -> StepInfo:
    """Add a shell command to the build graph.

    See [`step()`][stepup.core.api.step] for the documentation of all optional arguments
    and the return value.

    Parameters
    ----------
    command
        The command to execute, which will be interpreted by the shell.
    """
    return step(
        f"runsh {command}",
        inp=inp,
        env=env,
        out=out,
        vol=vol,
        workdir=workdir,
        optional=optional,
        pool=pool,
        block=block,
    )


def runpy(
    command: str,
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = "./",
    optional: bool = False,
    pool: str | None = None,
    block: bool = False,
):
    """Add a Python command to the build graph.

    See [`step()`][stepup.core.api.step] for the documentation of all optional arguments
    and the return value.

    Parameters
    ----------
    command
        The path of the script and its command line arguments.
        Local imports will be detected and amended as inputs to the script.
    """
    return step(
        f"runpy {command}",
        inp=inp,
        env=env,
        out=out,
        vol=vol,
        workdir=workdir,
        optional=optional,
        pool=pool,
        block=block,
    )


def plan(
    subdir: str,
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    optional: bool = False,
    pool: str | None = None,
    block: bool = False,
) -> StepInfo:
    """Run a `plan.py` script in a subdirectory.

    Parameters
    ----------
    subdir
        The subdirectory in which another `plan.py` script can be found.
        The file must be executable and have `#!/usr/bin/env python3` as its first line.
        A trailing slash is added when not present.
    inp, env, out, vol
        See the [`step()`][stepup.core.api.step] function for more information.
        (Rarely needed for planning steps.)
    optional
        See the [`step()`][stepup.core.api.step] function for more information.
        (Rarely needed for planning steps.)
        Use with care, since the nodes created by plan script will be unknown upfront
        and cannot therefore imply the necessity of an optional plan step.
    pool
        See the [`step()`][stepup.core.api.step] function for more information.
        (Rarely needed for planning steps.)
    block
        See the [`step()`][stepup.core.api.step] function for more information.
        (Rarely needed for planning steps.)

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    return runpy(
        "./plan.py",
        inp=["plan.py", *string_to_list(inp)],
        env=env,
        out=out,
        vol=vol,
        workdir=subdir,
        optional=optional,
        pool=pool,
        block=block,
    )


def copy(src: str, dst: str, *, optional: bool = False, block: bool = False) -> StepInfo:
    """Add a step that copies a file.

    Parameters
    ----------
    src
        This must be a file. Environment variables are substituted.
    dst
        This can be a file or a directory. Environment variables are substituted.
        If it is a directory, it must have a trailing slash.
    optional
        When True, the file is only copied when needed as input for another step.
    block
        When True, the step will always remain pending.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    amended_env_vars = set()
    with subs_env_vars() as subs:
        src = subs(src)
        dst = subs(dst)
    path_src = mynormpath(src)
    path_dst = make_path_out(src, dst, None)
    amend(env=amended_env_vars)
    return step(
        "copy ${inp} ${out}",
        inp=path_src,
        out=path_dst,
        optional=optional,
        block=block,
    )


def mkdir(dirname: str, *, optional: bool = False, block: bool = False) -> StepInfo:
    """Make a directory.

    Parameters
    ----------
    dirname
        The director to create.
        A trailing slash is added when not present.
        Environment variables are substituted.
    optional
        When True, the directory is only created when needed by other steps.
    block
        When True, the step will always remain pending.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    amended_env_vars = set()
    with subs_env_vars() as subs:
        dirname = subs(dirname)
    if not dirname.endswith("/"):
        dirname += "/"
    dirname = mynormpath(dirname)
    amend(env=amended_env_vars)
    return step(f"mkdir {dirname}", out=dirname, optional=optional, block=block)


def getenv(
    name: str,
    default: Path | str | None = None,
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

    Notes
    -----
    The optional arguments of this function have changed in StepUp 2.0.0.
    """
    path = path or back or multi
    value = os.getenv(name, default)
    # Do not amend environment variables set by the worker.
    # See stepup.core.worker.WorkerHandler.run
    if name not in ["HERE", "ROOT", "STEPUP_STEP_I", "STEPUP_STEP_INP_DIGEST"]:
        amend(env=name)
    if multi:
        if value is None:
            return []
        value = [item.strip() for item in value.split(":")]
        with subs_env_vars() as subs:
            value = [subs(item) for item in value if len(item) > 0]
        if back:
            value = [translate_back(item) for item in value if len(item) > 0]
    elif path:
        if value is None:
            raise ValueError(f"Undefined shell variable: {name}. Cannot create path.")
        with subs_env_vars() as subs:
            value = subs(value)
        if back:
            value = translate_back(value)
    return value


def call(
    executable: str,
    *,
    prefix: str | None = None,
    fmt: str = "auto",
    inp: Sequence | str | bool | None = None,
    env: Collection[str] | str = (),
    out: Sequence | str | bool | None = None,
    vol: Collection[str] | str = (),
    workdir: str = "./",
    optional: bool = False,
    pool: str | None = None,
    block: bool = False,
    pars: dict[str] | None = None,
    **kwargs,
) -> StepInfo:
    """Call an executable with a set of serialized arguments.

    This function assumes that the executable implements StepUp's
    [call protocol](../getting_started/call.md).

    Parameters
    ----------
    executable
        The path of a local executable script to call.
        Environment variables are substituted.
        The path of the executable is assumed to be relative to this directory.
    prefix
        The prefix used to construct filenames of the input (serialized arguments)
        and optionally output file (serialized return value).
        If absent, the prefix is the stem of the executable.
    fmt
        The format used for serialization of arguments (and optionally return values).
        Can be `"auto"`, `"json"` or `"pickle"`.
        In case `"auto"`, the `"json"` format is used,
        unless that fails, then `"pickle"` is used as the fallback option.
        If input or output files are given, the format is deduced from their extension.
    inp
        The path of the input file:

        - If `None`: The arguments are JSON serialized and passed to the script on the command line.
          If the types of the keyword arguments are incompatible with JSON,
          a pickle file is created whose filename is derived from `prefix`.
        - If `True`: an input file is always written to a path derived from `prefix` and `fmt`,
          even if no keyword arguments are given.
        - If `str`: an input file is written if some extra `**kwargs` are given,
          and `fmt` is deduced from the extension.
          Without keyword arguments, the input file is assumed to be the output of another step.
        - If `Sequence`, the first item is used according to one of the previous points,
          depending on its type.
          Remaining items are add to the `inp` argument of the `step()` function,
          and are added to `kwargs['inp']`.
    env
        See the [`step()`][stepup.core.api.step] function for more information.
    out
        The path of the output file:

        - If `None`: the script may write an output file. (This is the most flexible option.)
          The output path is derived from `prefix` and `fmt`.
          The script is called with arguments `--out={path_out}` and `--amend-out`,
          so the script can decide whether to write the output file.
        - If `str`: the script is called with the argument `--out={path_out}`
          and is expected to create this output file unconditionally.
          (No `amend(out=path_out)` is needed.)
        - If `True`, similar to the previous, except that
          the output path is derived from `prefix` and `fmt`.
        - If `False`, the script is not called with `--out`
          and is not expected to write an output file.
          (This is useful to keep things minimal.)
        - If `Sequence`, the first item is used according one of the previous points,
          depending on its type.
          Remaining items are add to the `out` argument of the `step()` function,
          and are added to `kwargs['out']`.

    vol
        See the [`step()`][stepup.core.api.step] function for more information.
    workdir
        See the [`step()`][stepup.core.api.step] function for more information.
    optional
        See the [`step()`][stepup.core.api.step] function for more information.
    pool
        See the [`step()`][stepup.core.api.step] function for more information.
    block
        See the [`step()`][stepup.core.api.step] function for more information.
    pars
        A dictionary with additional parameters for the script.
        They will be merged with the arguments in `kwargs`.
        (This can be useful to pass arguments whose name coincide with the arguments above.)
    kwargs
        If given, these are serialized to the input file.
        If absent, no input file is written unless `inp` is `True`.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Notes
    -----
    This is an experimental feature introduced in StepUp 2.0.0.
    It may undergo significant revisions in future 2.x releases.

    When the `inp`, `env`, `out` and `vol` arguments contain items,
    they are also included in the keyword arguments passed to the script.
    However, they do not count as extra keyword arguments to determine if an input file
    must be written when `inp` is a string or a sequence of strings.

    When using the call protocol, it is recommended to add the following lines to `.gitignore`:

    ```
    *_inp.json
    *_inp.pickle
    *_out.json
    *_out.pickle
    ```
    """
    # Preprocess inp and out in case they are lists.
    if not isinstance(inp, str) and isinstance(inp, Sequence):
        other_inp = inp[1:]
        inp = inp[0]
    else:
        other_inp = []
    if not isinstance(out, str) and isinstance(out, Sequence):
        other_out = out[1:]
        out = out[0]
    else:
        other_out = []

    if fmt not in ["auto", "json", "pickle"]:
        raise ValueError(f"Invalid format for serialization of arguments: {fmt}.")
    if inp not in [None, True] and not isinstance(inp, str):
        raise ValueError("Invalid value for _inp. Must be None, True, str or Sequence[str].")
    if not (out in [None, True, False] or isinstance(out, str)):
        raise ValueError("Invalid value for _out. Must be None, True, False, str or Sequence[str].")
    if prefix is None:
        prefix = Path(executable).stem
    with subs_env_vars() as subs:
        executable = subs(executable)
        workdir = subs(workdir)

    # Determine the format from given filenames
    if (isinstance(inp, str) or isinstance(out, str)) and fmt != "auto":
        raise ValueError("When specifying input or output files, the format cannot be set.")
    if fmt == "auto":
        if isinstance(inp, str):
            fmt = Path(inp).suffix[1:]
        elif isinstance(out, str):
            fmt = Path(out).suffix[1:]

    # Write the input file
    serial = None
    if pars is not None:
        kwargs.update(pars)
    if len(other_inp) > 0:
        kwargs.setdefault("inp", []).extend(other_inp)
    if len(other_out) > 0:
        kwargs.setdefault("out", []).extend(other_out)
    if len(kwargs) > 0:
        if fmt in ["json", "auto"]:
            try:
                serial = json.dumps(kwargs, indent=None if inp is None else 2)
                fmt = "json"
            except TypeError:
                if fmt == "auto":
                    fmt = "pickle"
                else:
                    raise
        if fmt == "pickle":
            serial = pickle.dumps(kwargs)

    if fmt == "auto":
        fmt = "json"
    if fmt not in ["json", "pickle"]:
        raise ValueError(f"Invalid format for serialization of arguments: {fmt}.")

    # Prepare arguments for the step function
    step_kwargs = {
        "inp": [*other_inp],
        "env": env,
        "out": [*other_out],
        "vol": vol,
        "workdir": workdir,
        "optional": optional,
        "pool": pool,
        "block": block,
    }

    # Input handling
    command = format_command(executable)
    path_inp = f"{prefix}_inp.{fmt}" if ((fmt == "pickle" and inp is None) or inp is True) else inp
    if serial is not None:
        # Provide input some way.
        if path_inp is None:
            command += " " + shlex.quote(serial)
        else:
            amend(out=path_inp)
            if isinstance(serial, str):
                with open(path_inp, "w") as fh:
                    fh.write(serial)
            else:
                with open(path_inp, "bw") as fh:
                    fh.write(serial)
    if isinstance(path_inp, str):
        # There is an input file, either created here or elsewhere.
        step_kwargs["inp"].insert(0, path_inp)
        command += f" --inp={shlex.quote(path_inp)}"

    # Output handling
    path_out = f"{prefix}_out.{fmt}" if (out is None or out is True) else out
    if isinstance(path_out, str):
        # The output file is created here.
        command += f" --out={shlex.quote(path_out)}"
        if out is None:
            command += " --amend-out"
        else:
            step_kwargs["out"].insert(0, path_out)

    # Finally, create a step
    step_kwargs.setdefault("inp", []).append(executable)
    if executable.endswith(".py"):
        return runpy(command, **step_kwargs)
    return runsh(command, **step_kwargs)


def script(
    executable: str,
    *,
    step_info: str | None = None,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
    workdir: str = "./",
    optional: bool = False,
    pool: str | None = None,
    block: bool = False,
) -> StepInfo:
    """Run the executable with a single argument `plan` in a working directory.

    This function assumes that the executable implements StepUp's
    [script protocol](../getting_started/script_single.md).

    Parameters
    ----------
    executable
        The path of a local executable that will be called with the argument `plan`.
        The file must be executable.
        The path of the script is assumed to be relative to this directory.
    step_info
        When given, the steps generated in the plan part of the executable are written
        to this `step_info` file. (See [stepup.core.stepinfo][] module for the file format.)
        This filename is relative to the work directory.
    inp, env, out, vol
        See the [`step()`][stepup.core.api.step] function for more information.
    workdir
        See the [`step()`][stepup.core.api.step] function for more information.
    optional
        See the [`step()`][stepup.core.api.step] function for more information.
    pool
        See the [`step()`][stepup.core.api.step] function for more information.
    block
        See the [`step()`][stepup.core.api.step] function for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Notes
    -----
    - The arguments `inp`, `env`, `out`, `vol` and `pool` are rarely needed for script steps.
      They only apply to the plan stage of the script, not the run stage.
    - The `inp` argument may be useful when the planning is configured by some input files.
    - The optional argument never applies to the plan stage,
      and is passed on the the run stage.
    """
    with subs_env_vars() as subs:
        executable = subs(executable)
        workdir = subs(workdir)
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
        "optional": optional,
        "pool": pool,
        "block": block,
    }
    if executable.endswith(".py"):
        return runpy(command, **step_kwargs)
    return runsh(command, **step_kwargs)


def loadns(
    *paths_variables: str, dir_out: str | None = None, do_amend: bool = True
) -> argparse.Namespace:
    """Load variable from Python, JSON, TOML or YAML files and put them in a namespace.

    Parameters
    ----------
    paths_variables
        paths of Python, JSON, TOML or YAML files containing variable definitions.
        They are loaded in the given order, so later variable definitions may overrule earlier ones.
    dir_out
        This is used to translate paths defined in the variables files
        (relative to parent of the variable file)
        to paths relative to the parent of the output of the rendering task.
        If not given, the current working directory is used.
        This is only relevant for variables loaded from Python files.
    do_amend
        If ``True``, All loaded files are amended as inputs to the current step.

    Returns
    -------
    variables
        A namespace with the variables.
    """
    if dir_out is None:
        dir_out = Path.cwd()
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
                if isinstance(value, Path):
                    value = value.relpath(dir_out)
                variables[name] = value
        else:
            raise ValueError(f"unsupported variable file format: {path_var}")
    if do_amend:
        amend(inp=paths_variables)
    return argparse.Namespace(**variables)


def render_jinja(
    *args: str | dict,
    mode: str = "auto",
    optional: bool = False,
    block: bool = False,
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
    optional
        If `True`, the step is only executed when needed by other steps.
    block
        If `True`, the step will always remain pending.

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
    args = ["render-jinja", "${inp}", "${out}"]
    if mode != "auto":
        args.append(f"--mode={mode}")
    if len(variables) > 0:
        args.append("--json=" + shlex.quote(json.dumps(variables)))
    return step(
        " ".join(args),
        inp=[path_template, *paths_variables],
        out=path_out,
        optional=optional,
        block=block,
    )


#
# API development utilities
#


@contextlib.contextmanager
def subs_env_vars() -> Iterator[Callable[[str | None], str | None]]:
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


#
# Internal stuff
#


class DeferredNotConfirmedError(Exception):
    """Raised deferred glob matches cannot be confirmed."""


def _confirm_missing(to_check: list[tuple[str, FileHash]] | None):
    """Confirm initially missing files and send the updates to the director."""
    # When the RPC_CLIENT is a dummy, to_check may be `None`.
    if to_check is not None and len(to_check) > 0:
        checked = []
        for tr_path, old_file_hash in to_check:
            new_file_hash = old_file_hash.regen(translate_back(tr_path))
            if new_file_hash != old_file_hash:
                checked.append((tr_path, new_file_hash))
        if len(checked) > 0:
            RPC_CLIENT.call.confirm(checked)


def _check_deferred(to_check: list[tuple[str, FileHash]] | None, step_i: int | None = None):
    """Check file, update hashes of existing ones, and send the updates to the director."""
    if to_check is not None and len(to_check) > 0:
        # Select matches of the deferred glob that exist and update their hashes.
        checked = []
        errors = ["Invalid deferred glob matches:"]
        for tr_path, old_file_hash in to_check:
            new_file_hash = old_file_hash.regen(translate_back(tr_path))
            if new_file_hash != old_file_hash:
                checked.append((tr_path, new_file_hash))
            if new_file_hash.is_unknown:
                errors.append(f"{tr_path} (MISSING)")
        if len(checked) > 0:
            RPC_CLIENT.call.confirm(checked)
        if len(errors) > 1:
            message = "\n".join(errors)
            if step_i is not None:
                RPC_CLIENT.call.reschedule_step(step_i, message)
            raise DeferredNotConfirmedError(message)


def _check_inp_paths(inp_paths: Iterable[Path]):
    """Check the validity of the input paths."""
    for inp_path in inp_paths:
        message = check_inp_path(inp_path)
        if message is not None:
            raise ValueError(f"{message}: {inp_path}")


def get_rpc_client(socket: str | None = None):
    """Try setting up a Synchronous RPC client or fall back to the dummy client if that fails."""
    stepup_director_socket = os.getenv("STEPUP_DIRECTOR_SOCKET", socket)
    if stepup_director_socket is None:
        return DummySyncRPCClient()
    return SocketSyncRPCClient(stepup_director_socket)


RPC_CLIENT = get_rpc_client()


def _get_step_i() -> int:
    """Get the current step node index from the STEPUP_STEP_I environment variable."""
    step_i = os.getenv("STEPUP_STEP_I")
    if step_i is None:
        if isinstance(RPC_CLIENT, DummySyncRPCClient):
            return -1
        raise RuntimeError("The STEPUP_STEP_I environment variable is not defined.")
    return int(step_i)
