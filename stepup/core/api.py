# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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

This module should not be imported by other stepup.core modules, except for stepup.interact.
"""

import contextlib
import os
from collections.abc import Callable, Collection, Iterable, Iterator

import attrs
from path import Path

from .nglob import NGlobMulti
from .rpc import DummySyncRPCClient, SocketSyncRPCClient
from .utils import (
    CaseSensitiveTemplate,
    check_inp_path,
    lookupdict,
    make_path_out,
    mynormpath,
    myrelpath,
)

__all__ = (
    # Basic API
    "static",
    "glob",
    "StepInfo",
    "step",
    "pool",
    "amend",
    # Composite API
    "plan",
    "copy",
    "mkdir",
    "getenv",
    "script",
    # Utilities for API development
    "subs_env_vars",
    "translate",
    # For stepup.core.interact
    "RPC_CLIENT",
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
        check_inp_paths(su_paths)
        # Translate paths to directory working dir and make RPC call
        tr_paths = sorted(translate(path) for path in su_paths)
        RPC_CLIENT.call.static(_get_step_key(), tr_paths)


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

    if _defer:
        if _required:
            raise ValueError("Combination of options not supported: _defer=True, _required=True")
        if len(subs) > 0:
            raise ValueError("Named wildcards are not supported in deferred globs.")
        tr_patterns = [translate(su_pattern) for su_pattern in su_patterns]
        tr_inp_check = RPC_CLIENT.call.defer(_get_step_key(), tr_patterns)
        if tr_inp_check is not None:
            # Verify that matches of the deferred glob exist.
            lo_inp_check = [translate_back(inp_path) for inp_path in tr_inp_check]
            check_inp_paths(lo_inp_check)
            RPC_CLIENT.call.confirm_deferred(tr_inp_check)
        return None

    # Collect all matches
    nglob_multi = NGlobMulti.from_patterns(su_patterns, subs)
    nglob_multi.glob()
    if _required and len(nglob_multi.results) == 0:
        raise FileNotFoundError("Could not find any matching paths on the filesystem.")

    # Send static paths
    static_paths = nglob_multi.files()
    if len(static_paths) > 0:
        check_inp_paths(static_paths)
        tr_static_paths = [translate(static_path) for static_path in static_paths]
        RPC_CLIENT.call.static(_get_step_key(), tr_static_paths)

    # Unstructure the nglob_multi and translate all paths before sending it to the director.
    lookup = lookupdict()
    ngm_data = nglob_multi.unstructure(lookup)
    tr_strings = [str(translate(path)) for path in lookup.get_list()]
    RPC_CLIENT.call.nglob(_get_step_key(), ngm_data, tr_strings)
    return nglob_multi


def _str_to_list(arg: Collection[str] | str) -> list[str]:
    return [arg] if isinstance(arg, str) else list(arg)


@attrs.define
class StepInfo:
    """The `step()` function returns an instance of this class to help defining follow-up steps.

    This object will not contain any information that is amended while the step is executed.
    It only holds information known at the time the step is defined.
    """

    key: str = attrs.field()
    """The key of the step in StepUp's workflow."""

    inp: list[Path] = attrs.field()
    """List of input paths of the step."""

    env: list[str] = attrs.field()
    """List of environment values used by the step."""

    out: list[Path] = attrs.field()
    """List of output paths of the step."""

    vol: list[Path] = attrs.field()
    """List of volatile output paths of the step."""

    def filter_inp(self, *patterns: str, **subs: str):
        """Return an `NGlobMulti` object with matching results from `self.inp`."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(self.inp)
        return ngm

    def filter_out(self, *patterns: str, **subs: str):
        """Return an `NGlobMulti` object with matching results from `self.out`."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(self.out)
        return ngm

    def filter_vol(self, *patterns: str, **subs: str):
        """Return an `NGlobMulti` object with matching results from `self.val`."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(self.vol)
        return ngm


def step(
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
    """Add a step to the build graph.

    Parameters
    ----------
    command
        Command to execute (in the working directory of the director).
    inp
        File(s) required by the step, relative to `workdir`.
        Can be files or directories (trailing slash).
    env
        Environment variable(s) to which the step is sensitive.
        If they change, or when they are (un)defined, the step digest will change,
        such that the step cannot be skipped.
    out
        File(s) created by the step, relative to `workdir`.
        These can be files or directories (trailing slash).
    vol
        Volatile file(s) created by the step, relative to `workdir`.
        These can be files only.
    workdir
        The directory where the command must be executed.
        A trailing slash is added when not present.
        (The default is the current directory.)
    optional
        When set to True, the step is only executed when required by other mandatory steps.
    pool
        Restricts execution to a pool, optional.
    block
        When set to True, the step will always remain pending.
        This can be used to temporarily prevent part of the workflow from executing.

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
    in the command are substituted by white-space concatenated list of `inp`, `out` and
    `vol`, respectively.
    Relative paths in `inp`, `out` and `env` are interpreted in the current working directory.
    Before substitution, they are rewritten as paths relative to the workdir.
    (Amended inputs and outputs are never substituted this way because they are yet unknown.)
    """
    # Pre-process the arguments for the Director process.
    inp_paths = _str_to_list(inp)
    env_vars = _str_to_list(env)
    out_paths = _str_to_list(out)
    vol_paths = _str_to_list(vol)
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
    command = CaseSensitiveTemplate(command).safe_substitute(
        inp=" ".join(su_inp_paths),
        out=" ".join(su_out_paths),
        vol=" ".join(su_vol_paths),
    )

    # Look for inputs that match deferred globs and check their existence
    # before making them static.
    tr_inp_check = RPC_CLIENT.call.filter_deferred(tr_inp_paths)
    if tr_inp_check is not None:
        lo_inp_check = [translate_back(inp_path, su_workdir) for inp_path in tr_inp_check]
        check_inp_paths(lo_inp_check)
        RPC_CLIENT.call.confirm_deferred(tr_inp_check)

    # Finally create the step.
    step_key = RPC_CLIENT.call.step(
        _get_step_key(),
        command,
        tr_inp_paths,
        env_vars,
        tr_out_paths,
        tr_vol_paths,
        tr_workdir,
        optional,
        pool,
        block,
    )

    # Return a StepInfo instance to facilitate the definition of follow-up steps
    return StepInfo(step_key, su_inp_paths, env_vars, su_out_paths, su_vol_paths)


def pool(name: str, size: int):
    """Define a pool with given size or change an existing pool size.

    Parameters
    ----------
    name
        The name of the pool.
    size
        The pool size.
    """
    RPC_CLIENT.call.pool(_get_step_key(), name, size)


def amend(
    *,
    inp: Collection[str] | str = (),
    env: Collection[str] | str = (),
    out: Collection[str] | str = (),
    vol: Collection[str] | str = (),
) -> bool:
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

    Returns
    -------
    keep_going
        True when the additional inputs are available and the step can safely use them.
        False otherwise, meaning the step can exit early and will be rescheduled later.

    Notes
    -----
    Environment variables in the `inp`, `out` and `vol` paths are substituted in the same way
    as in the `step()` function. The used variables are added to the env_vars argument.

    """
    # Pre-process the arguments for the Director process.
    inp_paths = _str_to_list(inp)
    env_vars = _str_to_list(env)
    out_paths = _str_to_list(out)
    vol_paths = _str_to_list(vol)
    if all(len(collection) == 0 for collection in [inp_paths, env_vars, out_paths, vol_paths]):
        return True
    env_vars = set(env_vars)
    with subs_env_vars() as subs:
        su_inp_paths = [subs(inp_path) for inp_path in inp_paths]
        tr_inp_paths = [translate(inp_path) for inp_path in su_inp_paths]
        tr_out_paths = [translate(subs(out_path)) for out_path in out_paths]
        tr_vol_paths = [translate(subs(vol_path)) for vol_path in vol_paths]

    # Look for inputs that match deferred globs and check their existence
    # before making them static.
    tr_inp_check = RPC_CLIENT.call.filter_deferred(tr_inp_paths)
    if tr_inp_check is not None:
        lo_inp_check = [translate_back(inp_path) for inp_path in tr_inp_check]
        check_inp_paths(lo_inp_check)
        RPC_CLIENT.call.confirm_deferred(tr_inp_check)

    # Finally, amend for real.
    keep_going = RPC_CLIENT.call.amend(
        _get_step_key(),
        tr_inp_paths,
        sorted(env_vars),
        tr_out_paths,
        tr_vol_paths,
    )
    if keep_going is None:
        # When the RPC_CLIENT is a dummy, it always returns None.
        # In this case, we're assuming the users calls the script
        # manually and made sure all the required files are present.
        keep_going = True
    if keep_going:
        # Double check that all inputs are indeed present.
        check_inp_paths(su_inp_paths)
    return keep_going


#
# Composite functions, created with the functions above.
#


def plan(subdir: str, block: bool = False) -> StepInfo:
    """Run a `plan.py` script in a subdirectory.

    Parameters
    ----------
    subdir
        The subdirectory in which another `plan.py` script can be found.
        The file must be executable and have `#!/usr/bin/env python` as its first line.
        A trailing slash is added when not present.
    block
        When True, the step will always remain pending.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    with subs_env_vars() as subs:
        subdir = subs(subdir)
    return step("./plan.py", inp="plan.py", workdir=subdir, block=block)


def copy(src: str, dst: str, optional: bool = False, block: bool = False) -> StepInfo:
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
    return step("cp -aT ${inp} ${out}", inp=path_src, out=path_dst, optional=optional, block=block)


def mkdir(dirname: str, optional: bool = False, block: bool = False) -> StepInfo:
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
    return step(f"mkdir -p {dirname}", out=dirname, optional=optional, block=block)


def getenv(name: str, default: str | None = None, is_path: bool = False) -> str | Path:
    """Get an environment variable and amend the current step with the variable name.

    Parameters
    ----------
    name
        The name of the environment variable, which is retrieved with `os.getenv`.
    default
        The value to return when the environment variable is unset.
    is_path
        Set to True if the variable taken from the environment is assumed to be a path.
        Shell variables are substituted (once) in such paths.
        If the path is relative, it is assumed to be relative to the StepUp's working directory.
        In this case, translated to become usable from the working directory of the caller.

    Returns
    -------
    value
        The value of the environment variable.
        If `is_path` is set to `True`, this is a `Path` instance.
        Otherwise, the result is a string.
    """
    value = os.getenv(name, default)
    names = [name]
    if is_path:
        value = Path(value)
        if not value.isabs():
            value = mynormpath(os.getenv("ROOT", ".") / Path(value))
            names.append("ROOT")
        with subs_env_vars() as subs:
            value = subs(value)
    amend(env=names)
    return value


def script(
    executable: str, workdir: str = "./", optional: bool = False, block: bool = False
) -> StepInfo:
    """Run the executable with a single argument `plan` in a working directory.

    Parameters
    ----------
    executable
        The path of a local executable that will be called with the argument `plan`.
        The file must be executable.
    workdir
        The subdirectory in which the script is to be executed.
        A trailing slash is added when not present.
        The path of the executable is assumed to be relative to this directory.
    optional
        When True, the steps planned by the executable are made optional.
        The planning itself is never optional.
    block
        When True, the planning will always remain pending.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    with subs_env_vars() as subs:
        executable = subs(executable)
        workdir = subs(workdir)
    command = f"./{executable} plan"
    if optional:
        command += " --optional"
    return step(command, inp=[executable], workdir=workdir, block=block)


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


def translate(path: str, workdir: str = ".") -> Path:
    """Normalize the path and, if relative, make it relative to `ROOT`.

    If the environment variable `HERE` is not set, it is derived from `STEPUP_ROOT` if set.

    Parameters
    ----------
    path
        The path to translate. If relative, it assumed to be relative to the working directory.
    workdir
        The work directory. If relative, it is assumed to be relative to `HERE`

    Returns
    -------
    translated_path
        A path that can be interpreted in the working directory of the StepUp director.
    """
    path = mynormpath(path)
    if not path.isabs():
        workdir = mynormpath(workdir)
        if workdir.isabs():
            path = workdir / path
        else:
            stepup_root = Path(os.getenv("STEPUP_ROOT", "./"))
            here = os.getenv("HERE")
            if here is None:
                here = myrelpath("./", stepup_root)
            path = myrelpath(mynormpath(stepup_root / here / workdir / path), stepup_root)
    return path


def translate_back(path: str, workdir: str = ".") -> Path:
    """If relative, make it relative to work directory, assuming it is relative to `ROOT`.

    If the environment variable `HERE` is not set, it is derived from `STEPUP_ROOT` if set.

    Parameters
    ----------
    path
        The path to translate. If relative, it is assumed to be relative to `ROOT`.
    workdir
        The working directory. If relative, it is assumed to be relative to `HERE`.

    Returns
    -------
    back_translated_path
        A path that can be interpreted in the working directory.
    """
    path = mynormpath(path)
    workdir = mynormpath(workdir)
    if path.isabs():
        if workdir.isabs() and path.startswith(workdir):
            path = myrelpath(path, workdir)
    else:
        here = os.getenv("HERE")
        if here is None:
            stepup_root = os.getenv("STEPUP_ROOT", "./")
            here = myrelpath("./", stepup_root)
        path = myrelpath(path, here / workdir)
    return path


def check_inp_paths(inp_paths: Collection[Path]):
    """Check the validity of the input paths."""
    for inp_path in inp_paths:
        message = check_inp_path(inp_path)
        if message is not None:
            raise ValueError(f"{message}: {inp_path}")


#
# Internal stuff
#


def _get_rpc_client():
    """Try setting up a Synchronous RPC client or fall back to the dummy client if that fails."""
    stepup_director_socket = os.getenv("STEPUP_DIRECTOR_SOCKET")
    if stepup_director_socket is None:
        return DummySyncRPCClient()
    return SocketSyncRPCClient(stepup_director_socket)


RPC_CLIENT = _get_rpc_client()


def _get_step_key():
    """Get the current step key from the STEPUP_STEP_KEY environment variable."""
    stepup_step_key = os.getenv("STEPUP_STEP_KEY")
    if stepup_step_key is None:
        if isinstance(RPC_CLIENT, DummySyncRPCClient):
            return "dummy:"
        raise RuntimeError("The STEPUP_STEP_KEY environment variable is not defined.")
    return stepup_step_key
