# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Application programming interface to the director.

To keep things simple, it is assumed that one Python process only communicates with one director.

This module should not be imported by other stepup.core modules, safe for some notable exceptions:

- `stepup.core.interact`
- `stepup.core.extapi`
- Inside some functions, e.g. `driver()` in `stepup.core.call`.

All path arguments accept either a `str` or any `os.PathLike` object (such as a `pathlib.Path`).
Note that `pathlib` normalizes away leading `./` and trailing `/` affixes at construction time.
For arguments where these affixes are significant
(the `dst` of `copy`, local executables, and the path variants of `getenv`),
pass a `str` or a `path.Path` to preserve them.
"""

import contextlib
import json
import keyword
import logging
import math
import os
import shlex
import sys
import tomllib
from collections.abc import Iterable, Iterator
from runpy import run_path
from types import SimpleNamespace
from typing import Any

import yaml
from path import Path

from .cattrs import json_converter, yaml_converter
from .enums import Need
from .exceptions import (
    AmendWhileHoldingError,
    EnvVarError,
    InputNotFoundError,
    PathError,
    StepUpError,
)
from .extapi import subs_env_vars
from .nglob import NGlobMulti
from .path import (
    StrPath,
    apply_affixes,
    coerce_path,
    coerce_paths,
    coerce_paths2,
    coerce_str,
    get_affixes,
    make_path_out,
    translate,
    translate_back,
)
from .rpc import DummySyncRPCClient, SocketSyncRPCClient
from .step import RESERVED_ENV_VARS
from .stepinfo import StepInfo
from .utils import extract_env_overrides, format_command, parse_resources, string_to_list

__all__ = (
    "RPC_CLIENT",
    "amend",
    "call",
    "copy",
    "dumpns",
    "get_job_i",
    "get_rpc_client",
    "getenv",
    "getinfo",
    "glob",
    "graph",
    "hold",
    "loadns",
    "plan",
    "render_jinja",
    "run",
    "script",
    "shq",
    "static",
    "step",
)

logger = logging.getLogger(__name__)


#
# Basic API
#


def static(*paths: StrPath | Iterable[StrPath]) -> None:
    """Declare static paths.

    Parameters
    ----------
    *paths
        One or more paths to declare as static, relative to the current working directory.
        Arguments may also be iterables of strings.
        Each string must refer to an existing file or directory and can be one of:

        1. A file: declared immediately as a static path,
           unless it already belongs to a static tree, in which case this is a no-op.
        2. A directory: registered as a static tree; files within it are lazily
           declared static the first time they are used as step inputs.

        Within a single `static()` call, directory arguments are always registered
        before file arguments, regardless of the order in which they were given.
        A single call declaring both a tree and a file it contains is therefore
        equivalent to declaring the tree first in a separate, earlier call.

    Raises
    ------
    PathError
        When a path does not exist.
    EnvVarError
        When an environment variable in a path is undefined,
        or when a path contains an invalid variable identifier.
    GraphError
        When a directory overlaps with an existing static tree,
        or when it already contains a file declared before it.

    Notes
    -----
    Environment variables in `paths` are substituted immediately,
    and the variables referenced are added to the calling step's `env_deps` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.
    """
    # Turn paths into one big list.
    paths = coerce_paths2(paths)

    # Avoid empty RPC calls.
    if len(paths) > 0:
        # Perform env var substitutions.
        with subs_env_vars() as subs:
            su_paths = [subs(path).normpath() for path in paths]
        # Sanity checks
        su_file_paths, su_dir_paths = _check_inp_paths(su_paths, allow_dirs=True)
        # Static trees must reach the director before any file it contains:
        # declaring a file first would make it look like it predates the tree,
        # which the director rejects. This ordering is load-bearing, not incidental,
        # which is also why a file already covered by a tree declared in this same call
        # is skipped as a no-op by `Workflow.declare_unconfirmed` below.
        if len(su_dir_paths) > 0:
            # Translate paths to make them relative to the working directory of the director.
            tr_dir_paths = sorted(translate(su_dir_path) for su_dir_path in su_dir_paths)
            # Declare the static trees; matching existing files are hashed and confirmed
            # in the background by the director, same as above.
            RPC_CLIENT.call.static_trees(get_job_i(), tr_dir_paths)
        if len(su_file_paths) > 0:
            # Translate paths to make them relative to the working directory of the director.
            tr_file_paths = sorted(translate(su_file_path) for su_file_path in su_file_paths)
            # Declare the files unconfirmed; the director hashes and confirms them in the
            # background, off this call's critical path.
            RPC_CLIENT.call.declare_unconfirmed(get_job_i(), tr_file_paths)


def glob(*patterns: StrPath, **subs: str) -> NGlobMulti:
    """Return file and directory matches of glob patterns, and declare static files.

    StepUp registers that the caller uses these patterns,
    so it can make the calling step pending when new matches appear in future runs.
    A file match is declared static, unless it already belongs to a static tree
    (declared with `static()`), which owns it instead.
    A directory match is only accepted when it lies inside a static tree:
    outside one, StepUp has no evidence that the directory is source material
    rather than a step's build product, so the match set could depend on build progress.

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

    Raises
    ------
    StepUpError
        When no patterns are given.
    GraphError
        When a directory match does not lie inside a static tree.

    Notes
    -----
    Multiple patterns are matched *jointly*: only combinations of files whose
    named wildcard substitutions are mutually consistent are returned.
    For independent patterns, separate `glob` calls are more efficient.

    Environment variables in `patterns` are substituted before matching,
    and the variables referenced are added to the calling step's `env_deps` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.
    """
    if len(patterns) == 0:
        raise StepUpError("At least one path is required for glob.")
    # Substitute environment variables.
    # Affixes are captured before normpath(), which would otherwise strip them,
    # and re-applied after, so a trailing separator survives normalization.
    with subs_env_vars() as subs_path:
        su_patterns = []
        for pattern in patterns:
            su_pattern = subs_path(pattern)
            prefix, suffix = get_affixes(su_pattern)
            su_patterns.append(apply_affixes(su_pattern.normpath(), prefix, suffix))

    # StepUp needs to know the patterns, so it can identify new files matching the
    # patterns in future runs. Trailing separators are preserved because translate()
    # normalizes them away, and a trailing separator distinguishes a directory pattern.
    tr_patterns = []
    for su_pattern in su_patterns:
        prefix, suffix = get_affixes(su_pattern)
        tr_patterns.append(apply_affixes(translate(su_pattern), prefix, suffix))

    # Collect all matches
    nglob_multi = NGlobMulti.from_patterns(su_patterns, subs)
    nglob_multi.glob()

    # Translate all matches, keeping track of which ones are directories.
    # Trailing separators are preserved for the same reason as for the patterns above.
    # Existence is guaranteed by nglob's own filesystem walk, so `_check_inp_path`
    # here only classifies files versus directories; it cannot raise.
    tr_all_paths = []
    tr_dir_paths = []
    for nglob_single in nglob_multi.nglob_singles:
        for paths in nglob_single.results.values():
            for path in paths:
                prefix, suffix = get_affixes(path)
                tr_path = apply_affixes(translate(path), prefix, suffix)
                tr_all_paths.append(tr_path)
                if _check_inp_path(path, return_dir=True):
                    tr_dir_paths.append(tr_path)

    # One call: the director decides which file matches are already owned by a static
    # tree (skipped), which directory matches lie inside one (accepted),
    # and which directory matches do not (raises `GraphError`).
    RPC_CLIENT.call.nglob(get_job_i(), tr_patterns, subs, tr_all_paths, tr_dir_paths)

    # Done
    return nglob_multi


def step(
    command: StrPath,
    *,
    inp: Iterable[StrPath] | StrPath = (),
    env: Iterable[str] | str = (),
    out: Iterable[StrPath] | StrPath = (),
    vol: Iterable[StrPath] | StrPath = (),
    workdir: StrPath = ".",
    need: Need = Need.DEFAULT,
    resources: dict[str, int] | str | None = None,
    shell: bool = False,
    env_overrides: dict[str, str] | None = None,
    duration: float | None = None,
) -> StepInfo:
    """Add a step to the build graph.

    Parameters
    ----------
    command
        Command to execute (in the given working directory).
        The command is sent to the director verbatim: no placeholder or environment-variable
        substitution is performed on it. Use [`shq()`][stepup.core.api.shq] to embed `inp`,
        `out`, or `vol` paths, e.g. `step(f"cat {shq(inp)} > {shq(out)}", inp=inp, out=out)`.
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
    env_overrides
        Step-specific environment variable overrides for the child process,
        e.g. `{"OMP_NUM_THREADS": "4"}`.
        These overrides (the variable **values** for the child process)
        are distinct from `env` (the variable **names** the step is sensitive to):
        a variable may not appear in both, otherwise a `StepUpError` is raised.
        [`run()`][stepup.core.api.run] and [`plan()`][stepup.core.api.plan] populate this
        automatically from leading `VAR=value` assignments in `command`;
        callers of `step()` directly must pass this argument explicitly.
    duration
        An initial estimate of the step's wall time in seconds, used by the scheduler
        (when `--duration` is enabled) to prioritize execution order before any measurement
        is available. Once the step has run, the scheduler overwrites this with the measured
        duration. When not given, a new step starts with a default estimate of `1.0`; a
        recycled step keeps its previously measured (or given) duration.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Raises
    ------
    StepUpError
        When `command` is empty, when an env override collides with an `env` dependency
        or a reserved variable name, when a resource quantity is not a strictly
        positive integer, or when `duration` is not a finite non-negative number.
    PathError
        When `inp`, `out`, or `vol` contain a directory.

    Notes
    -----
    Environment variables in `inp`, `out`, `vol`, and `workdir` are substituted immediately,
    and the variables referenced are added to the calling step's `env_deps` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.

    Relative paths in `inp`, `out`, and `vol` are relative to the working directory of the new step.
    """
    # Pre-process the arguments for the Director process.
    command = coerce_str(command)
    inp_paths = coerce_paths(inp)
    env_deps = string_to_list(env)
    out_paths = coerce_paths(out)
    vol_paths = coerce_paths(vol)

    # Validate the command
    if len(command.strip()) == 0:
        raise StepUpError("The command must not be empty.")

    # Validate the duration.
    # `bool` is a subclass of `int`, so it is excluded explicitly to catch callers confusing
    # this keyword-only argument for one of `step()`'s several `bool` flags (e.g. `optional`).
    # `inf` is rejected too: an infinite duration estimate cannot be a genuine measurement
    # and almost certainly indicates a caller bug, e.g. an unguarded division.
    if duration is not None and (
        isinstance(duration, bool)
        or not isinstance(duration, int | float)
        or math.isnan(duration)
        or math.isinf(duration)
        or duration < 0
    ):
        raise StepUpError(f"Invalid duration: {duration!r}. Must be a non-negative number.")

    # Validate the environment overrides against the env dependencies and reserved names.
    if env_overrides is not None:
        overlap = set(env_deps) & set(env_overrides)
        if overlap:
            raise StepUpError(
                "Variable(s) cannot be both an env dependency and a env_overrides override: "
                + ", ".join(sorted(overlap))
            )
        reserved = set(env_overrides) & RESERVED_ENV_VARS
        if reserved:
            raise StepUpError(
                "Variable(s) set by StepUp cannot be overridden: " + ", ".join(sorted(reserved))
            )

    with subs_env_vars() as subs:
        su_inp_paths = [subs(inp_path).normpath() for inp_path in inp_paths]
        su_out_paths = [subs(out_path).normpath() for out_path in out_paths]
        su_vol_paths = [subs(vol_path).normpath() for vol_path in vol_paths]
        su_workdir = subs(workdir).normpath()
    _check_no_directories(su_inp_paths, su_workdir)
    _check_no_directories(su_out_paths, su_workdir)
    _check_no_directories(su_vol_paths, su_workdir)
    tr_inp_paths = [translate(inp_path, su_workdir) for inp_path in su_inp_paths]
    tr_out_paths = [translate(out_path, su_workdir) for out_path in su_out_paths]
    tr_vol_paths = [translate(vol_path, su_workdir) for vol_path in su_vol_paths]
    tr_workdir = translate(su_workdir)

    # Interpret the resources string, if needed.
    if resources is None:
        resources = {}
    elif isinstance(resources, str):
        resources = parse_resources(resources)
    elif not isinstance(resources, dict):
        raise TypeError("The resources argument must be a dict, a string or None.")
    # At this stage, we do not allow non-positive quantities of resources.
    # `bool` is a subclass of `int` and is excluded explicitly, for the same reason as
    # the `duration` check above.
    for resource, quantity in resources.items():
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise StepUpError(
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

    # Finally create the step. Any inputs matching a static tree are declared UNCONFIRMED
    # and hashed/confirmed by the director in the background; a step consuming one simply
    # does not become runnable until that resolves (see scheduler.py).
    RPC_CLIENT.call.step(
        get_job_i(),
        command,
        tr_inp_paths,
        env_deps,
        tr_out_paths,
        tr_vol_paths,
        tr_workdir,
        need.value,
        resources,
        shell,
        env_overrides,
        duration,
    )

    # Return a StepInfo instance to facilitate the definition of follow-up steps
    return StepInfo(command, su_inp_paths, env_deps, su_out_paths, su_vol_paths, tr_workdir)


def call(
    executable_: StrPath,
    function_: str,
    *,
    inp: Iterable[StrPath] | StrPath = (),
    env: Iterable[str] | str = (),
    out: Iterable[StrPath] | StrPath = (),
    vol: Iterable[StrPath] | StrPath = (),
    workdir: StrPath = ".",
    optional: bool = False,
    planning: bool = False,
    resources: dict[str, int] | str | None = None,
    args_file: StrPath | None = None,
    duration: float | None = None,
    **kwargs: Any,
) -> StepInfo:
    """Register a step that calls a named function in an executable.

    Parameters
    ----------
    executable_
        Path to the script or binary to invoke.
        Must contain a path separator (e.g. `./script.py` or `sub/script.py`)
        and must not be an absolute path.
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
    duration
        See [`step()`][stepup.core.api.step] for more information.
    **kwargs
        Additional keyword arguments forwarded to the function.
        Must be serializable to JSON by the `cattrs` JSON converter.

    Returns
    -------
    step_info
        Holds relevant information of the registered step.

    Raises
    ------
    StepUpError
        When `optional` and `planning` are both `True`,
        when `function_` is not a valid Python function name,
        when the inline JSON string exceeds 128 KiB (use `args_file` instead),
        or when `args_file` has an unrecognized extension.
    PathError
        When `executable_` does not contain a path separator or is absolute.
    """
    # Validate mutually exclusive flags.
    if optional and planning:
        raise StepUpError("optional and planning are mutually exclusive")

    # Normalize the executable, preserving any prefix/suffix for later re-application.
    executable_ = coerce_path(executable_)
    prefix, suffix = get_affixes(executable_)
    executable_ = apply_affixes(executable_.normpath(), prefix, suffix)

    # Perform environment variable substitutions before building the command.
    # This is somewhat redundant with the substitutions performed in `step()`.
    with subs_env_vars() as subs:
        inp = [subs(inp_path).normpath() for inp_path in coerce_paths(inp)]
        out = [subs(out_path).normpath() for out_path in coerce_paths(out)]
        workdir = subs(workdir).normpath()
        if args_file is not None:
            args_file = subs(args_file).normpath()

    # Validate executable path format.
    if os.sep not in executable_:
        raise PathError(
            f"executable_ must contain a path separator (e.g. './script.py'), got: {executable_!r}"
        )

    # Validate the executable is not absolute.
    if os.path.isabs(executable_):
        raise PathError(f"executable_ must not be an absolute path, got: {executable_!r}")

    # Validate the function name. A valid Python identifier that is not a reserved
    # keyword can never contain shell metacharacters, so it is safe to interpolate
    # unquoted into the command below.
    if not (function_.isidentifier() and not keyword.iskeyword(function_)):
        raise StepUpError(f"function_ must be a valid Python function name, got: {function_!r}")

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
            raise StepUpError(
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
        duration=duration,
    )


# A history used to avoid amending the same information twice.
# This effectively reduces the amount of amend API calls.
AMEND_HISTORY = {
    "inp": set(),
    "env": set(),
    "out": set(),
    "vol": set(),
}


class _HoldState:
    """How many `hold()` blocks the current process is nested inside of.

    Incremented/decremented by `hold()`'s `__enter__`/`__exit__`. `amend()` only checks
    `holding > 0`, so re-entrant nesting is transparent to it.
    """

    holding = 0


_HOLD_STATE = _HoldState()


def amend(
    *,
    inp: Iterable[StrPath] | StrPath = (),
    env: Iterable[str] | str = (),
    out: Iterable[StrPath] | StrPath = (),
    vol: Iterable[StrPath] | StrPath = (),
) -> None:
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
    PathError
        When `inp`, `out`, or `vol` contain a directory.
    InputNotFoundError
        When amended inputs are not yet available.
        Let this exception propagate — do not catch it.
        The director postpones the step once the missing inputs become available.
        Note this call blocks until any amended input still matching an unconfirmed static
        tree entry is hashed, so it may take a while for large files.
    AmendWhileHoldingError
        When `inp` is non-empty and this is called anywhere in the calling step's execution
        while a `with hold():` block of that same step is still open.
        This holds even for an input that would have resolved instantly and harmlessly:
        the check does not look at whether the input is actually available, only at whether
        the calling step is holding.
        `env`, `out`, and `vol` are never involved in this check: unlike `inp`, none of them
        can depend on a held-back step's output, so an `env`/`out`/`vol`-only amend can never
        deadlock and is always allowed, even while holding.
        Move the code that calls `amend(inp=...)` before entering the `with hold():` block
        instead.

    Notes
    -----
    Environment variables in `inp`, `out`, and `vol` are substituted immediately,
    and the variables referenced are added to the calling step's `env_deps` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.

    Calling `amend(inp=...)` before reading additional input files is recommended, but not required:
    it is also safe to call `amend()` afterward.
    A file that is missing, or that was built too recently to be trusted
    (e.g. still being written by its producer step while it was read),
    causes the step to be postponed rather than to fail outright.
    Calling `amend()` early remains preferable where practical,
    since it avoids the wasted work of a postponed step.

    For additional output files, `amend(out=...)` or `amend(vol=...)` is required before writing.
    These will raise an exception if the amended outputs collide with files declared elsewhere
    in the workflow, preventing accidental overwrites of other step's files.

    Repeated calls are safe: items already amended in prior calls are silently skipped.
    """
    # Pre-process the arguments for the Director process.
    inp_paths = coerce_paths(inp)
    env_deps = string_to_list(env)
    out_paths = coerce_paths(out)
    vol_paths = coerce_paths(vol)
    if all(len(collection) == 0 for collection in [inp_paths, env_deps, out_paths, vol_paths]):
        # Nothing is actually being amended: e.g. `run.py`/`render_jinja.py` call
        # `amend(inp=get_local_import_paths())` unconditionally after a Python step runs,
        # and that list can be empty. Such a no-op call must not trip the hold() guard
        # below, or an unrelated held step could fail through no fault of its own code.
        return
    if _HOLD_STATE.holding > 0 and len(inp_paths) > 0:
        # Only `inp` can deadlock a hold(): the director can only postpone an amend() over
        # unavailable/unfresh *inputs* (see `Workflow.amend_step`), and the input's producer
        # could be a step held back by this same `hold()` block. `env`, `out`, and `vol` are
        # never checked against another step's output, so they can never trigger that
        # postponement and are safe to amend while holding, e.g. internal callers such as
        # `getenv()` (env-only) and `dumpns()` (out-only).
        raise AmendWhileHoldingError(
            "amend() cannot be called with `inp` while this step has an open hold() block. "
            "Call the amend-triggering code before entering the `with hold():` block."
        )
    env_deps = set(env_deps)
    with subs_env_vars() as subs:
        su_inp_paths = {subs(inp_path).normpath() for inp_path in inp_paths}
        su_out_paths = {subs(out_path).normpath() for out_path in out_paths}
        su_vol_paths = {subs(vol_path).normpath() for vol_path in vol_paths}
    # The checks use the substituted paths, not the translated ones below:
    # they look at the file system, which this process sees relative to its own
    # working directory, not relative to the director's.
    _check_no_directories(su_inp_paths)
    _check_no_directories(su_out_paths)
    _check_no_directories(su_vol_paths)
    tr_inp_paths = {translate(inp_path) for inp_path in su_inp_paths}
    tr_out_paths = {translate(out_path) for out_path in su_out_paths}
    tr_vol_paths = {translate(vol_path) for vol_path in su_vol_paths}

    # Filter out previously amended information
    tr_inp_paths.difference_update(AMEND_HISTORY["inp"])
    env_deps.difference_update(AMEND_HISTORY["env"])
    tr_out_paths.difference_update(AMEND_HISTORY["out"])
    tr_vol_paths.difference_update(AMEND_HISTORY["vol"])

    if (
        len(tr_inp_paths) == 0
        and len(env_deps) == 0
        and len(tr_out_paths) == 0
        and len(tr_vol_paths) == 0
    ):
        return

    # Finally, amend for real. This call may block while the director hashes any amended
    # input that still matches an unconfirmed static tree entry, which can exceed
    # STEPUP_SYNC_RPC_TIMEOUT for a large file, hence the disabled socket timeout.
    job_i = get_job_i()
    carry_on = RPC_CLIENT.call.amend(
        job_i,
        tr_inp_paths,
        sorted(env_deps),
        tr_out_paths,
        tr_vol_paths,
        _rpc_timeout=0,
    )
    if carry_on is False:
        raise InputNotFoundError("Amended inputs are not available yet.")

    # Double check that all inputs are indeed present.
    _check_inp_paths(su_inp_paths)

    # Update the amendment history
    AMEND_HISTORY["inp"].update(tr_inp_paths)
    AMEND_HISTORY["env"].update(env_deps)
    AMEND_HISTORY["out"].update(tr_out_paths)
    AMEND_HISTORY["vol"].update(tr_vol_paths)


@contextlib.contextmanager
def hold() -> Iterator[None]:
    """Hold back steps declared within this block and schedule them after the block exits.

    Use this to wrap a batch of `run()`/`step()`/`plan()` calls (typically in a `plan.py`)
    so the whole batch becomes simultaneously eligible for dispatch once the block closes,
    instead of each child being dispatched as soon as it is declared. This lets the existing
    duration-based scheduling order the batch by cost, rather than by declaration order.

    `hold()` is re-entrant: nesting `with hold():` blocks (directly, or through a helper
    function called while already holding) is safe. Children declared anywhere in the nested
    scopes stay held back until the **outermost** block exits, not the innermost one.

    No `amend(inp=...)` call may be made anywhere in the calling step's execution while any
    `hold()` block is open, not even one that would resolve instantly and harmlessly: it would
    risk a deadlock, since the step cannot release the hold without the amended input, and the
    input's producer cannot run until the hold is released. See `amend()`'s
    `AmendWhileHoldingError`. `amend(env=..., out=..., vol=...)` carries no such risk and
    remains allowed while holding, since none of those can depend on a held-back step's output.

    If the block raises, releasing the hold is still attempted, but a failure of that release
    call never replaces the original exception: it is logged instead, so the real cause of the
    failure is not masked by an unrelated RPC problem. `_HOLD_STATE.holding` is only decremented
    once `release()` is confirmed to have succeeded, so `amend()`'s guard correctly stays active
    if the release call could not be confirmed.
    """
    job_i = get_job_i()
    RPC_CLIENT.call.hold(job_i)
    _HOLD_STATE.holding += 1
    try:
        yield
    finally:
        # Capture this before the nested try/except below: once that except clause catches a
        # release() failure, sys.exc_info() would reflect that new exception instead of one
        # already propagating from `yield`.
        had_exception = sys.exc_info()[0] is not None
        try:
            RPC_CLIENT.call.release(job_i)
        except Exception:
            if had_exception:
                logger.warning(
                    "release() failed while another exception was propagating from a "
                    "`with hold():` block; suppressing the release() failure to avoid "
                    "masking the original error.",
                    exc_info=True,
                )
            else:
                raise
        else:
            _HOLD_STATE.holding -= 1


def getinfo() -> StepInfo:
    """Get the information of the current step.

    Returns
    -------
    step_info
        Holds relevant information of the current step, useful for defining follow-up steps.
        For consistency with other functions in this module, the `inp`, `out` and `vol`
        paths are relative to the working directory of the step.
    """
    step_info = RPC_CLIENT.call.getinfo(get_job_i())
    # Update paths to make them relative to the working directory of the step.
    step_info.inp = sorted(translate_back(inp) for inp in step_info.inp)
    step_info.out = sorted(translate_back(out) for out in step_info.out)
    step_info.vol = sorted(translate_back(vol) for vol in step_info.vol)
    return step_info


def graph(prefix: StrPath) -> None:
    """Write the workflow graph files in text and dot formats."""
    return RPC_CLIENT.call.graph(coerce_path(prefix))


def shq(paths: StrPath | Iterable[StrPath]) -> str:
    """Shell-quote and join one or more paths for embedding in a command string.

    Parameters
    ----------
    paths
        A single path or an iterable of paths.

    Returns
    -------
    quoted
        The paths, shell-quoted and space-separated,
        ready to be embedded in a `command` passed to `step()`, `run()`, or `plan()`.

    Notes
    -----
    Environment variables in `paths` are substituted immediately,
    and the variables referenced are added to the calling step's `env_deps` list.
    These substitutions are based on the state of `os.environ` in the calling script,
    at the time this function is called, not when the step is executed.

    A subset of a path list can be quoted independently,
    e.g. `shq(inp[:3])` and `shq(inp[3:])` to spread `inp` over two different
    command-line options.
    """
    su_paths = coerce_paths(paths)
    with subs_env_vars() as subs:
        su_paths = [subs(path).normpath() for path in su_paths]
    return shlex.join(su_paths)


#
# Composite functions, created with the functions above.
#


def run(
    command: StrPath,
    *,
    inp: Iterable[StrPath] | StrPath = (),
    env: Iterable[str] | str = (),
    out: Iterable[StrPath] | StrPath = (),
    vol: Iterable[StrPath] | StrPath = (),
    workdir: StrPath = ".",
    optional: bool = False,
    shell: bool = False,
    resources: dict[str, int] | str | None = None,
    duration: float | None = None,
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

        When `shell=False`, the command may start with one or more `VAR=value` assignments,
        e.g. `OMP_NUM_THREADS=4 ./run.py`. These are stripped from the command and applied as
        step-specific environment variable overrides when the step runs (see `step()`'s
        `env_overrides`). With `shell=True`, assignments are left in the command for the
        shell to interpret.
        Putting the same variable in both the shell prefix and env is invalid
        and only detected with `shell=False`.

        When the first word, after stripping any leading `VAR=value` assignments,
        contains a `/` and is not an absolute path (e.g. `./script.py`, `subdir/tool`),
        it is automatically added as an input dependency.
        Bare command names like `echo` or absolute paths like `/usr/bin/gcc` are not added.

        Python detection uses the `.py` file extension only,
        so it works even when the script does not yet exist (e.g. it is an output of another step).
        `shell=True` takes precedence and disables Python auto-detection.

        Use [`shq()`][stepup.core.api.shq] to embed `inp`, `out`, or `vol` paths in the
        command, e.g. `run(f"./script.py {shq(inp)}", inp=inp)`.
    inp, env, out, vol, workdir, optional, resources, duration
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    command, exe, env_overrides = _prepare_run_command(
        command, shell=shell, need_relative_exe=False
    )
    if exe is not None:
        inp = [exe, *coerce_paths(inp)]
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
        env_overrides=env_overrides,
        duration=duration,
    )


def plan(
    command: StrPath,
    *,
    inp: Iterable[StrPath] | StrPath = (),
    env: Iterable[str] | str = (),
    out: Iterable[StrPath] | StrPath = (),
    vol: Iterable[StrPath] | StrPath = (),
    workdir: StrPath = ".",
    resources: dict[str, int] | str | None = None,
    duration: float | None = None,
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

        The command may start with one or more `VAR=value` assignments,
        e.g. `OMP_NUM_THREADS=4 ./plan.py`. These are stripped from the command and applied as
        step-specific environment variable overrides when the step runs (see `step()`'s
        `env_overrides`).
        Putting the same variable in both the shell prefix and env is invalid and raises an error.

        Use [`shq()`][stepup.core.api.shq] to embed `inp`, `out`, or `vol` paths in the
        command, e.g. `plan(f"./plan.py {shq(inp)}", inp=inp)`.
    inp, env, out, vol, workdir, resources, duration
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.
    """
    # Note that we do not use `run()` here because we need to set `need=Need.PLAN`.
    command, exe, env_overrides = _prepare_run_command(command, shell=False, need_relative_exe=True)
    inp = [exe, *coerce_paths(inp)]
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
        env_overrides=env_overrides,
        duration=duration,
    )


def copy(
    src: StrPath,
    dst: StrPath,
    *,
    optional: bool = False,
    resources: dict[str, int] | str | None = None,
    duration: float | None = None,
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
        Note that the trailing slash is not supported by `pathlib.Path`.
        It is recommended to use a string or `path.Path` for `dst` in this case.
    optional, resources, duration
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Notes
    -----
    Environment variables in `src` and `dst` are substituted immediately,
    and the variables referenced are added to the calling step's `env_deps` list with `amend()`.
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
        f"cp -p {shq(src)} {shq(dst)}",
        inp=src,
        out=dst,
        need=Need.OPTIONAL if optional else Need.DEFAULT,
        resources=resources,
        shell=False,
        duration=duration,
    )


def getenv(
    name: str,
    default: StrPath | None = None,
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

    Raises
    ------
    EnvVarError
        When `path`, `back`, or `multi` is `True` and the environment variable is unset
        and no `default` is given.
    """
    path = path or back or multi
    if default is not None:
        default = coerce_str(default)
    value = os.getenv(name, default)
    # Do not amend environment variables set for the step by the executor.
    # See stepup.core.executor.Executor._run_command
    if name not in RESERVED_ENV_VARS:
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
            raise EnvVarError(f"Undefined shell variable: {name}. Cannot create path.")
        with subs_env_vars() as subs:
            value = subs(value)
        prefix, suffix = get_affixes(value)
        value = value.normpath()
        if back:
            value = translate_back(value)
        value = apply_affixes(value, prefix, suffix)
    return value


def script(
    executable: StrPath,
    *,
    step_info: StrPath | None = None,
    inp: Iterable[StrPath] | StrPath = (),
    env: Iterable[str] | str = (),
    out: Iterable[StrPath] | StrPath = (),
    vol: Iterable[StrPath] | StrPath = (),
    workdir: StrPath = ".",
    optional: bool = False,
    resources: dict[str, int] | str | None = None,
    duration: float | None = None,
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
    step_info
        When given, the steps generated in the plan part of the executable are written
        to this `step_info` file. (See [stepup.core.stepinfo][] module for the file format.)
        This filename is relative to the work directory.
    inp, env, out, vol, workdir, optional, resources, duration
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
    # Normalize the executable, preserving any prefix/suffix for later re-application.
    executable = coerce_path(executable)
    prefix, suffix = get_affixes(executable)
    executable = apply_affixes(executable.normpath(), prefix, suffix)

    # Start building the command and the step inputs.
    command = format_command(executable) + " plan"
    out = coerce_paths(out)
    if step_info is not None:
        step_info = coerce_path(step_info)
        command += " --step-info=" + shlex.quote(step_info)
        out.append(step_info)
    if optional:
        command += " --optional"
    inp = coerce_paths(inp)
    inp.append(executable)
    step_kwargs = {
        "inp": inp,
        "env": env,
        "out": out,
        "vol": vol,
        "workdir": workdir,
        "need": Need.PLAN,
        "resources": resources,
        "duration": duration,
    }
    # Note that we do not use `run()` here because we need to set `need=Need.PLAN`.
    return step(command, **step_kwargs)


def loadns(
    *paths_variables: StrPath, dir_out: StrPath | None = None, do_amend: bool = True
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

    Raises
    ------
    StepUpError
        When a file in `paths_variables` does not have a `.json`, `.toml`, `.yaml`,
        `.yml`, or `.py` extension.
    """
    # Process arguments
    dir_out = Path.cwd() if dir_out is None else coerce_path(dir_out)
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
            raise StepUpError(f"unsupported variable file format: {path_var}")
    if do_amend:
        amend(inp=paths_variables)

    # Return as a namespace
    return SimpleNamespace(**variables)


def dumpns(path: StrPath, data: dict[str, Any] | SimpleNamespace, *, do_amend: bool = True) -> None:
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
    StepUpError
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
        raise StepUpError(f"dumpns: unsupported file format: {path_obj.suffix!r}")


def render_jinja(
    *args: StrPath | dict[str, Any],
    mode: str = "auto",
    optional: bool = False,
    resources: dict[str, int] | str | None = None,
    duration: float | None = None,
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
    optional, resources, duration
        See [`step()`][stepup.core.api.step] for more information.

    Returns
    -------
    step_info
        Holds relevant information of the step, useful for defining follow-up steps.

    Raises
    ------
    StepUpError
        When `mode` is not one of `'auto'`, `'plain'`, or `'latex'`,
        or when no variables are given, neither as a file nor as a dictionary.

    Notes
    -----
    At least some variables must be given, either as a file containing variables or as a dictionary.
    """
    # Parse the positional arguments
    if len(args) < 3:
        raise TypeError(
            "At least three positional arguments must be given: "
            "the template, at least one file or dict with variables, and the destination."
        )
    path_template = args[0]
    if not isinstance(path_template, (str, os.PathLike)):
        raise TypeError("The template argument must be a path.")
    path_template = coerce_path(path_template)
    dest = args[-1]
    if not isinstance(dest, (str, os.PathLike)):
        raise TypeError("The destination argument must be a path.")
    dest = coerce_path(dest)
    variables = {}
    paths_variables = []
    for arg in args[1:-1]:
        if isinstance(arg, dict):
            variables.update(arg)
        elif isinstance(arg, (str, os.PathLike)):
            paths_variables.append(coerce_path(arg))
        else:
            raise TypeError("The variables arguments must be paths or dictionaries.")

    # Parse other arguments.
    if mode not in ["auto", "plain", "latex"]:
        raise StepUpError(f"Unsupported mode {mode!r}. Must be one of 'auto', 'plain', 'latex'")
    if len(paths_variables) == 0 and len(variables) == 0:
        raise StepUpError("At least one file with variable definitions needed.")
    path_out = make_path_out(path_template, dest, None)
    paths_inp = [path_template, *paths_variables]

    # Create the command
    args = ["sc-render-jinja", shq(paths_inp), shq(path_out)]
    if mode != "auto":
        args.append(f"--mode={mode}")
    if len(variables) > 0:
        args.append("--json=" + shlex.quote(json.dumps(variables)))
    return step(
        " ".join(args),
        inp=paths_inp,
        out=path_out,
        need=Need.OPTIONAL if optional else Need.DEFAULT,
        resources=resources,
        duration=duration,
    )


#
# Internal stuff
#


def _check_inp_path(inp_path: Path, return_dir: bool = False) -> bool | None:
    """Check the validity of a single input path.

    Parameters
    ----------
    inp_path
        The input path to check.
    return_dir
        Whether to allow directories as valid input paths.
        If set, a boolean is returned indicating whether the input path is a directory.

    Returns
    -------
    is_dir
        Whether `inp_path` is a directory, or `None` when `return_dir` is not set.

    Raises
    ------
    PathError
        If the input path is a directory and `return_dir` is not set,
        or if the input path does not exist.
    """
    is_dir = inp_path.is_dir()
    if not return_dir:
        if inp_path.endswith(os.sep):
            raise PathError(f"Directory inputs are not supported: {inp_path}")
        if is_dir:
            raise PathError(f"Directory inputs are not supported: {inp_path}")
    if not inp_path.exists():
        raise PathError(f"Path does not exist: {inp_path}")
    return is_dir if return_dir else None


def _check_inp_paths(
    inp_paths: Iterable[Path], allow_dirs: bool = False
) -> tuple[list[Path], list[Path]]:
    """Check the validity of the input paths, splitting files from directories."""
    file_paths = []
    dir_paths = []
    for inp_path in inp_paths:
        is_dir = _check_inp_path(inp_path, return_dir=allow_dirs)
        (dir_paths if is_dir else file_paths).append(inp_path)
    return file_paths, dir_paths


def _check_no_directories(paths: Iterable[Path], workdir: StrPath = "."):
    """Check that the paths are not directories.

    A path is rejected both when it is spelled as a directory (trailing separator)
    and when it currently is one on disk.
    The latter catches e.g. `run("...", inp="some/dir")` at the call site in `plan.py`,
    instead of much later, when the director fails to hash it.

    Parameters
    ----------
    paths
        The paths to check.
    workdir
        The working directory that relative `paths` are interpreted in,
        itself relative to the current working directory.

    Raises
    ------
    PathError
        If one of the paths is a directory.
    """
    workdir = coerce_path(workdir)
    for path in paths:
        if path.endswith(os.sep) or (workdir / path).is_dir():
            raise PathError(f"Directories are not allowed: {path}")


def _prepare_run_command(
    command: StrPath, *, shell: bool, need_relative_exe: bool
) -> tuple[str, str | None, dict[str, str] | None]:
    """Pre-process a `run()`/`plan()` command string.

    Extracts leading `VAR=value` assignments (unless `shell`) and detects a local relative
    executable as the first word following any such assignments.

    Parameters
    ----------
    command
        The raw command string.
    shell
        When `True`, leading `VAR=value` assignments are left in the command for the shell
        to interpret, and no overrides are extracted. They are still skipped over when
        looking for the executable, so their values are not mistaken for it.
    need_relative_exe
        When `True`, require the first word of the command to be a local relative executable
        (it contains a path separator and is not absolute), raising a `PathError` otherwise.
        When `False`, a missing relative executable is silently ignored.

    Raises
    ------
    StepUpError
        When the command cannot be split into words with `shlex` (e.g. unbalanced quotes).
    PathError
        When `need_relative_exe` is `True` and the first word is not a local relative executable.

    Returns
    -------
    command
        The command string with any leading assignments removed (when not `shell`).
    exe
        The local relative executable to add as an input, or `None`.
    env_overrides
        The extracted environment overrides, or `None`.
    """
    command = coerce_str(command)
    env_overrides, remaining = extract_env_overrides(command)
    if shell:
        # Leading assignments are left in the command for the shell to interpret.
        # They are only stripped off here to find the real first word for exe detection.
        env_overrides = None
    else:
        command = remaining
    try:
        parts = shlex.split(remaining)
    except ValueError as exc:
        raise StepUpError(
            f"Cannot parse command to detect the executable: {command} ({exc})"
        ) from exc
    exe = None
    first = parts[0] if len(parts) > 0 else ""
    if os.sep in first and not first.startswith(os.sep):
        exe = first
    elif need_relative_exe:
        raise PathError(
            "The command must be a relative path to a local executable, "
            f"containing at least one slash, e.g. './plan.py'. Got: {command}"
        )
    return command, exe, env_overrides


def get_rpc_client(socket: str | None = None) -> DummySyncRPCClient | SocketSyncRPCClient:
    """Try setting up a Synchronous RPC client or fall back to the dummy client if that fails."""
    stepup_director_socket = os.getenv("STEPUP_DIRECTOR_SOCKET", socket)
    if stepup_director_socket == "_invalid_socket_for_director_process_":
        raise RuntimeError("The RPC client is being used within the director process.")
    if stepup_director_socket is None:
        return DummySyncRPCClient()
    return SocketSyncRPCClient(stepup_director_socket)


RPC_CLIENT = get_rpc_client()


def get_job_i() -> int:
    """Get the current job id from the STEPUP_JOB_I environment variable."""
    job_i = os.getenv("STEPUP_JOB_I")
    if job_i is None:
        if not isinstance(RPC_CLIENT, SocketSyncRPCClient):
            return -1
        raise RuntimeError("The STEPUP_JOB_I environment variable is not defined.")
    return int(job_i)
