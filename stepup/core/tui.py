# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Own the `stepup build` command line, spawn the director subprocess, and supervise it.

This module involves a bi-directional RPC connection between the director and the TUI:

- Supervision means forwarding terminal signals and keystrokes to the director
  and translating its exit status into a `ReturnCode`.
  The TUI (and the `keyboard` task) connect to the director as clients.

- This module also serves the reporter RPC socket that the director connects back to,
  to report progress.
"""

import argparse
import asyncio
import contextlib
import decimal
import os
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
from collections.abc import AsyncGenerator, Callable
from decimal import Decimal
from typing import Self

import attrs
from path import Path

from .asyncio import stoppable_iterator, wait_for_path
from .cgroups import cgroup_scope_prefix
from .config import ConfigLoader
from .constants import (
    DIRECTOR_LOG,
    JOBLOG_CSV,
    PERF_DATA,
    PLAN_PY,
    SQLLOG_CSV,
    SQLLOG_JSON,
    STEPUP_DIR,
)
from .enums import ReturnCode
from .exceptions import RPCError, ToolError, UsageError
from .reporter import ReporterHandler
from .rpc import AsyncRPCClient, serve_socket_rpc
from .utils import (
    is_process_running,
    merge_resources,
    positive_int,
    query_director_log,
    scan_director_log,
    string_to_bool,
)
from .watcher import WATCHER_AVAILABLE

# The subcommands are referenced by string in `pyproject.toml`'s `stepup.tools` entry points.
__all__ = ("boot_subcommand", "build_subcommand")


#
# CLI definition: `stepup build` and its arguments
#


class MergeResourcesAction(argparse.Action):
    """Merge a `--resources` value into the accumulated one instead of replacing it.

    Argparse seeds the namespace with `action.default` before parsing,
    so on the first `-r` occurrence the accumulated value is already
    whatever `ConfigLoader.patch_parser` put there (config file and env var merged in).
    Repeated `-r` occurrences merge left to right on top of that,
    consistent with how config and env values combine.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        accumulated = getattr(namespace, self.dest)
        setattr(namespace, self.dest, merge_resources(accumulated, values))


def positive_decimal(value: str) -> Decimal:
    """Convert a command-line value to a strictly positive `Decimal`.

    Raises
    ------
    ValueError
        If the value is not a number or is not strictly positive.
    """
    try:
        number = Decimal(value)
    except decimal.InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if number <= 0:
        raise ValueError(f"must be strictly positive: {value!r}")
    return number


def _add_build_parser(subparsers, loader: ConfigLoader, name: str, help_text: str) -> None:
    """Register the build subparser under `name`.

    The argument definitions are identical for every subcommand name;
    only the subparser name and its help text differ.
    The parser's `prog` is pinned to `"stepup build"` instead of the default derived from `name`,
    because `ConfigLoader.patch_parser` derives the config section from `prog`.
    Configuration therefore always comes from the `"build"` section, regardless of `name`,
    so `stepup build` and its aliases share a single source of truth for configuration.
    The price is that an alias shows `usage: stepup build ...` in its help,
    which is a fair hint towards the canonical name.

    Parameters
    ----------
    subparsers
        The subparser to add the build tool to.
    loader
        The configuration loader to override the default configuration with config file values.
    name
        The subcommand name to register (e.g. `"build"` or `"boot"`).
    help_text
        The help text shown for this subcommand.
    """
    parser = subparsers.add_parser(
        name,
        prog="stepup build",
        help=help_text,
    )
    parser.add_argument(
        "targets",
        nargs="*",
        default=[],
        help="Build only these output files (and their required dependencies), "
        "instead of the full default workflow.",
    )
    group = parser.add_argument_group("build control")
    group.add_argument(
        "--clean",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Remove outdated output files.",
    )
    group.add_argument(
        "--duration",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Use the duration of steps to optimize the execution order.",
    )
    group.add_argument(
        "--explain-rerun",
        "-e",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Explain for every step with recorded info why it cannot be skipped.",
    )
    group.add_argument(
        "--fix-epoch",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Set the SOURCE_DATE_EPOCH environment variable to 315532800. "
        "This corresponds to 1980-01-01 00:00:00 UTC. "
        "(If the variable is already set, it will be used as-is.) ",
    )
    group.add_argument(
        "--jobs",
        "-j",
        type=positive_decimal,
        default=Decimal("1.0"),
        help="Number of jobs running in parallel. "
        "When given as a real number with digits after the decimal point, "
        "it is multiplied with the number of available cores. [default=%(default)s]",
    )
    group.add_argument(
        "--keep-going",
        "-k",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Keep building steps whose inputs remain available after another step fails, "
        "instead of draining the scheduler, like `make -k`. "
        "In-progress steps always finish regardless of this flag.",
    )
    group.add_argument(
        "--defer-cap",
        type=positive_int,
        default=100,
        help="Maximum number of consecutive defers (since the last success) before "
        "a step is failed instead of parked. A livelock guard. [default=%(default)s]",
    )
    group.add_argument(
        "--progress",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Report progress information in the terminal user interface. "
        "(This can be useful to simplify and reduce the output.)",
    )
    group.add_argument(
        "--resources",
        "-r",
        default=None,
        action=MergeResourcesAction,
        help="Available resources for steps, e.g. 'cpu:4,gpu:1,memgb:16'. "
        "Merged with (not overriding) config files, the STEPUP_BUILD_RESOURCES env var, "
        "and any earlier --resources on the command line.",
    )
    if WATCHER_AVAILABLE:
        group.add_argument(
            "--watch",
            "-w",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="StepUp will watch for file changes after all runnable steps have been executed. "
            "By pressing r, it will rerun steps that have become pending due to the file changes. "
            "(Only supported on Linux.)",
        )
        group.add_argument(
            "--watch-first",
            "-W",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="Start the builder after observing the first file change in watch mode. "
            "This implies --watch. (Only supported on Linux.)",
        )

    group = parser.add_argument_group("execution environment")
    group.add_argument(
        "--cgroup",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Launch the director in its own cgroup, so that peak memory usage of the director and "
        "its children can be measured accurately. (Supported on Linux with systemd only.) "
        "Fails if not supported.",
    )
    group.add_argument(
        "--forkserver",
        default=(sys.platform == "linux"),
        action=argparse.BooleanOptionalAction,
        help="Use a forkserver for Python step execution and file hashing "
        "to reduce startup overhead. [default: True on Linux, False elsewhere]",
    )
    group.add_argument(
        "--preload-modules",
        default=None,
        help="Comma-separated list of Python modules to pre-load into the forkserver. "
        "Only has effect when --forkserver is active. [default: none]",
    )

    group = parser.add_argument_group("diagnostics and profiling")
    group.add_argument(
        "--joblog",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Record job-execution events (created, started, ended, completed) to "
        f"{JOBLOG_CSV}, for diagnosing scheduler/executor dispatch overhead.",
    )
    group.add_argument(
        "--perf",
        type=positive_int,
        default=None,
        nargs="?",
        const=500,
        metavar="FREQ",
        help="Profile the director with perf, by default at a frequency of %(const)s Hz. "
        "(Only supported on Linux with perf installed.) "
        "Write --perf=FREQ when combining with build targets, "
        "since a bare --perf would otherwise consume the next target as the frequency.",
    )
    group.add_argument(
        "--sqllog",
        default=False,
        action=argparse.BooleanOptionalAction,
        help=f"Enable SQLite debug logging: append per-query timing rows to {SQLLOG_CSV} "
        f"as they execute, and write a query/call-site/plan index to {SQLLOG_JSON} "
        "when the director exits.",
    )
    group.add_argument(
        "--yappi",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Profile the director with Yappi (must be installed). "
        "This produces a .stepup/director.prof file that can be analyzed with "
        "tools like SnakeViz.",
    )

    loader.patch_parser(parser, merge_handlers={"resources": merge_resources})


def build_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    """Define command-line arguments for the build tool.

    Parameters
    ----------
    subparsers
        The subparser to add the build tool to.
    loader
        The configuration loader to override the default configuration with config file values.

    Returns
    -------
    tool_func
        The function to call with the parsed args to start building.
    """
    _add_build_parser(subparsers, loader, "build", "Build the StepUp workflow.")
    return _build_tool


def boot_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    """Define command-line arguments for the deprecated `boot` alias of `build`.

    Parameters
    ----------
    subparsers
        The subparser to add the boot tool to.
    loader
        The configuration loader to override the default configuration with config file values.

    Returns
    -------
    tool_func
        The function to call with the parsed args
        to print a deprecation warning and start building.
    """
    _add_build_parser(subparsers, loader, "boot", "Deprecated alias of 'stepup build'.")
    return _deprecated_boot_tool


#
# Entry points
#


def _build_tool(args: argparse.Namespace) -> int:
    """Run `stepup build`, reporting a `ToolError` as an error message instead of a traceback.

    Parameters
    ----------
    args
        The parsed `stepup build` command-line arguments.

    Returns
    -------
    returncode
        The exit code for the `stepup build` process.
    """
    try:
        return asyncio.run(_async_build(args))
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return ReturnCode.INTERNAL.value


def _deprecated_boot_tool(args: argparse.Namespace) -> int:
    """Run `stepup boot`, the deprecated alias of `stepup build`, after printing a warning.

    Parameters
    ----------
    args
        The parsed `stepup boot` command-line arguments.

    Returns
    -------
    returncode
        The exit code for the `stepup build` process.
    """
    print(
        "Warning: 'stepup boot' is deprecated; use 'stepup build' instead.",
        file=sys.stderr,
    )
    return _build_tool(args)


#
# The build driver
#


async def _async_build(args: argparse.Namespace) -> int:
    """Launch the director, forward keystrokes and terminal signals to it, and report progress.

    Sockets are placed in a per-run temp directory under `/tmp` rather than `${PWD}/.stepup/`.
    Unix domain socket paths have a strict limit of 104 bytes (macOS) / 108 bytes (Linux).
    Deeply nested project paths can easily breach this constraint.

    Parameters
    ----------
    args
        The parsed `stepup build` command-line arguments.

    Returns
    -------
    returncode
        The exit code for the `stepup build` process,
        combining what the director reported
        with `ReturnCode.INTERRUPTED` when a terminal signal was received
        and `ReturnCode.INTERNAL` when a debug build found problems in the director log.

    Raises
    ------
    ToolError
        If `plan.py` does not exist in the project root,
        or if a director is already running (see `_check_no_running_director`).
    """
    # Absolutize STEPUP_ROOT before changing directory,
    # so a relative path is interpreted against the original cwd.
    stepup_root = Path(os.getenv("STEPUP_ROOT", os.getcwd())).absolute()
    targets, target_dirs = _normalize_targets(args.targets, stepup_root)
    if stepup_root != Path.cwd():
        print("Changing to", stepup_root)
        stepup_root.cd()

    # Sanity check before creating a subdirectory.
    if not PLAN_PY.is_file():
        raise ToolError("File plan.py does not exist.")

    _reset_stepup_dir()

    with tempfile.TemporaryDirectory(prefix="stepup-") as dir_sockets:
        dir_sockets = Path(dir_sockets)

        # Create socket paths
        director_socket_path = dir_sockets / "director"
        reporter_socket_path = dir_sockets / "reporter"

        # Set up the reporter monitor.
        # Its lifetime is that of the socket directory, not that of the director subprocess,
        # so it is not owned by _supervise_director.
        stop_event = asyncio.Event()
        reporter_handler = ReporterHandler(args.progress, stop_event)
        task_reporter = asyncio.create_task(
            serve_socket_rpc(reporter_handler, reporter_socket_path, stop_event),
            name="reporter-rpc",
        )
        try:
            argv = _build_director_argv(
                args,
                targets,
                target_dirs,
                director_socket_path,
                reporter_socket_path,
                live_progress=reporter_handler.live_progress,
            )
            returncode = await _supervise_director(
                argv, director_socket_path, reporter_handler, stop_event
            )
        finally:
            # Both are called unconditionally,
            # i.e. also when an exception escapes the try block above.
            # Stopping the reporter stops the Live display and restores the cursor,
            # which the director's own `stop_reporting` RPC never got to do in that case.
            # `ReporterHandler.stop_reporting` already sets `stop_event` as a side effect;
            # setting it here as well keeps the await below from depending on that,
            # since it would hang forever if the event were ever left unset.
            reporter_handler.stop_reporting()
            stop_event.set()
            await task_reporter

    return returncode | _report_director_log_problems(reporter_handler)


def _normalize_targets(raw_targets: list[str], stepup_root: Path) -> tuple[list[Path], list[Path]]:
    """Resolve and validate `stepup build` target arguments against the project root.

    A raw target ending in `os.sep` is a directory target:
    every step under that subtree whose declared `need` is `DEFAULT` is elevated, best-effort
    (see `docs/advanced_topics/build_targets.md`).
    Everything else is an exact-file target, regardless of what exists on disk.
    The trailing slash is the only classifier; classification never looks at the file system.

    Parameters
    ----------
    raw_targets
        The raw target strings, as passed on the command line.
    stepup_root
        The absolute path of the StepUp project root.

    Returns
    -------
    targets
        The normalized, root-relative exact-file target paths.
    target_dirs
        The normalized, root-relative directory target paths, each carrying its trailing slash.

    Raises
    ------
    ToolError
        If a target is an empty string.
    """
    targets = []
    target_dirs = []
    for raw_target in raw_targets:
        if raw_target == "":
            raise ToolError("A target cannot be an empty string.")
        is_dir_target = raw_target.endswith(os.sep)
        target_abs = Path(raw_target).absolute()
        target_rel = target_abs.relpath(stepup_root).normpath()
        if is_dir_target:
            target_dirs.append(target_rel / "")
        else:
            targets.append(target_rel)
    return targets, target_dirs


def _check_no_running_director() -> None:
    """Refuse to start a build while another director owns this workflow.

    The check is deliberately conservative:
    an advertised pid that still exists is treated as a running director,
    even though the pid could have been reused,
    because two directors on one database are exactly what this guards against.

    Raises
    ------
    ToolError
        If a director socket from a previous run still exists
        and its process is (or may still be) alive.
    """
    path_old_socket, pid, _ = query_director_log(DIRECTOR_LOG)
    if path_old_socket is None:
        return
    if pid is None or is_process_running(pid):
        raise ToolError(
            f"A director may still be running (pid {pid}), "
            f"using socket {path_old_socket}. Stop it before starting a new build."
        )
    print(
        f"Ignoring stale socket {path_old_socket} of director pid {pid}, "
        "which is no longer running."
    )


def _reset_stepup_dir() -> None:
    """Refuse to start next to a running director, then remove the log files in `.stepup`.

    The order matters and is the reason these two steps live in one function:
    the running-director check reads `DIRECTOR_LOG`, which is one of the files removed.
    Checking second would make the check unconditionally pass.

    Raises
    ------
    ToolError
        If another director may still be running (see `_check_no_running_director`).
    """
    _check_no_running_director()
    STEPUP_DIR.makedirs_p()
    for path_log in STEPUP_DIR.glob("*.log"):
        path_log.remove_p()


def _build_director_argv(
    args: argparse.Namespace,
    targets: list[Path],
    target_dirs: list[Path],
    director_socket_path: Path,
    reporter_socket_path: Path,
    *,
    live_progress: bool,
) -> list[str]:
    """Translate the parsed `stepup build` arguments into the director's command line.

    Parameters
    ----------
    args
        The parsed `stepup build` command-line arguments.
        Not mutated.
    targets, target_dirs
        The normalized target paths, as returned by `_normalize_targets`.
    director_socket_path
        The socket at which the director will listen for instructions.
    reporter_socket_path
        The socket at which the reporter listens for updates from the director.
    live_progress
        Whether the director should send live step-count updates to the reporter.

    Returns
    -------
    argv
        The full command line to launch the director subprocess with.

    Raises
    ------
    RuntimeError
        If `args.cgroup` is set
        and `cgroup_scope_prefix()` cannot build a `systemd-run --scope` prefix
        (see its own `Raises` section).
    """
    argv = []
    if args.perf is not None:
        argv.extend(["perf", "record", "-F", str(args.perf), "-i", "-g", "-o", PERF_DATA])
    argv.append(sys.executable)
    if args.perf is not None:
        argv.extend(["-X", "perf"])
    argv.extend(
        [
            "-m",
            "stepup.core.director",
            director_socket_path,
            f"--reporter={reporter_socket_path}",
            f"--jobs={args.jobs}",
            f"--defer-cap={args.defer_cap}",
            f"--log-level={args.log_level}",
        ]
    )
    argv.extend(f"--target={target}" for target in targets)
    argv.extend(f"--target-dir={target_dir}" for target_dir in target_dirs)
    if args.forkserver:
        argv.append("--forkserver")
    if args.preload_modules:
        argv.append(f"--preload-modules={args.preload_modules}")
    if not args.clean:
        argv.append("--no-clean")
    if not args.duration:
        argv.append("--no-duration")
    if args.explain_rerun:
        argv.append("--explain-rerun")
    if args.keep_going:
        argv.append("--keep-going")
    if not args.fix_epoch:
        argv.append("--no-fix-epoch")
    if live_progress:
        argv.append("--live-progress")
    if args.resources:
        argv.append(f"--resources={args.resources}")
    if args.sqllog:
        argv.append("--sqllog")
    if args.joblog:
        argv.append("--joblog")
    if WATCHER_AVAILABLE:
        if args.watch:
            argv.append("--watch")
        if args.watch_first:
            argv.append("--watch-first")
    if args.yappi:
        argv.append("--yappi")
    if args.cgroup:
        cgroup_argv = cgroup_scope_prefix()
        argv = [*cgroup_argv, *argv, "--cgroup"]
    return argv


async def _supervise_director(
    argv: list[str],
    director_socket_path: Path,
    reporter_handler: ReporterHandler,
    stop_event: asyncio.Event,
) -> int:
    """Run the director subprocess to completion and translate its wait status.

    This creates the director's log file
    and owns the terminal signal handlers and the keyboard task,
    both of which are strictly scoped to the lifetime of the subprocess.
    The subprocess never outlives this function:
    whatever happens, it is waited for, and killed first if it is still running.

    Parameters
    ----------
    argv
        The director's command line, as returned by `_build_director_argv`.
    director_socket_path
        The socket at which the director will listen for instructions.
    reporter_handler
        The reporter used to inform the user about interruptions, keystrokes
        and a director killed by a signal.
    stop_event
        Event that ends the keyboard task, and that the caller uses to stop the reporter.

    Returns
    -------
    returncode
        The exit code for the `stepup build` process,
        as translated by `TerminalSignalHandler.translate_wait_status`.
    """
    task_keyboard = None
    raw_terminal = None
    try:
        with open(DIRECTOR_LOG, "w") as log_file:
            # The child gets a duplicate of the log file descriptor,
            # so this handle can be closed right after the spawn,
            # instead of being kept open for the whole build.
            process_director = await asyncio.create_subprocess_exec(
                *argv,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        # Install terminal signal handlers, to avoid internal tracebacks
        # when the user presses Ctrl-C or the process is killed by a SIGTERM.
        # The director aborts the build itself,
        # so this waits for it rather than exiting straight away,
        # which would cut its shutdown short.
        signal_handler = TerminalSignalHandler(reporter_handler, process_director)
        suspend_handler = SuspendHandler(reporter_handler, process_director)
        loop = asyncio.get_running_loop()
        # Wait for the director in a task,
        # so that the interactive setup below can race the appearance of its socket
        # against its exit.
        task_director = asyncio.create_task(process_director.wait(), name="director-wait")
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, signal_handler.handle, sig)
            loop.add_signal_handler(signal.SIGTSTP, suspend_handler.handle)
            # Set up keyboard interaction, or work non-interactively.
            if sys.stdin.isatty() and await _wait_for_director_socket(
                director_socket_path, stop_event, task_director
            ):
                # Raw mode is entered here, not inside the keyboard task,
                # so that it is already in place when that task starts,
                # and so that the suspend handler has a terminal to hand back to the shell.
                raw_terminal = RawTerminal(sys.stdin.fileno())
                raw_terminal.enter()
                suspend_handler.raw_terminal = raw_terminal
                task_keyboard = asyncio.create_task(
                    keyboard(director_socket_path, reporter_handler, stop_event),
                    name="keyboard",
                )
            wait_status = await task_director
        finally:
            signal_handler.cancel_kill()
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGTSTP):
                loop.remove_signal_handler(sig)
            if not task_director.done():
                # An exception escaped on the way to (or during) the await above.
                # The director must not survive this function:
                # the socket directory it is listening in is about to be removed,
                # and the next build would refuse to start
                # next to a director that is still running.
                with contextlib.suppress(ProcessLookupError):
                    process_director.kill()
                await task_director
        # The director normally calls reporter.stop_reporting() itself over RPC,
        # but a director that crashes or is killed never gets there.
        # Calling it here as well is idempotent,
        # and it stops the Live display before the report below,
        # so that the error line is not printed underneath a running progress bar.
        reporter_handler.stop_reporting()
        return signal_handler.translate_wait_status(wait_status)
    finally:
        if task_keyboard is not None:
            # The keyboard task reads keystrokes until stop_event is set.
            # The event is set here (idempotently, the caller sets it too)
            # because the await below would hang forever when an exception escapes the try block
            # before the `stop_reporting` call that normally sets it.
            stop_event.set()
            await task_keyboard
        if raw_terminal is not None:
            # After the keyboard task,
            # so that the reader thread cannot consume a keystroke in raw mode
            # after the terminal has been handed back to the shell.
            raw_terminal.leave()


async def _wait_for_director_socket(
    director_socket_path: Path, stop_event: asyncio.Event, task_director: asyncio.Task
) -> bool:
    """Wait until the director's socket exists, or until the director exits.

    Waiting on the socket alone would hang forever when the director exits before creating it,
    e.g. because its argparse rejected an option, a preloaded module failed to import,
    or a wrapper like `perf record` refused to start it.
    Nothing would set `stop_event` in that case:
    it is set by the director's own `shutdown` RPC or by `_async_build`'s `finally`,
    and neither can happen while control is stuck in this wait.

    Parameters
    ----------
    director_socket_path
        The path of the socket that the director creates when it is ready.
    stop_event
        Event that also ends the wait, e.g. when the build is shutting down.
    task_director
        The task waiting for the director subprocess to exit.

    Returns
    -------
    ready
        Whether the socket exists, i.e. whether the director can be talked to.
    """
    task_socket = asyncio.create_task(
        wait_for_path(director_socket_path, stop_event), name="wait-socket"
    )
    await asyncio.wait([task_socket, task_director], return_when=asyncio.FIRST_COMPLETED)
    if not task_socket.done():
        task_socket.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task_socket
    # Not task_socket.done(): wait_for_path also returns when stop_event is set,
    # without the socket having appeared.
    return director_socket_path.exists()


def _report_director_log_problems(reporter_handler: ReporterHandler) -> int:
    """Report the symptoms of internal problems that the director left in its log.

    The director exits with a zero return code after a successful build,
    even when it logged an error or abandoned a coroutine, task or thread along the way,
    so its log is the only place where such problems surface.
    It is scanned once, after the director has exited and written everything it had to say.

    Parameters
    ----------
    reporter_handler
        The reporter used to show the findings.

    Returns
    -------
    returncode
        `ReturnCode.INTERNAL` when problems were found and `STEPUP_DEBUG` is set,
        zero otherwise.
    """
    findings = scan_director_log(DIRECTOR_LOG)
    if len(findings) == 0:
        return 0
    pages = [("Director log", "\n".join(findings))]
    description = f"Problems logged in {DIRECTOR_LOG}"
    if string_to_bool(os.getenv("STEPUP_DEBUG", "0")):
        # Every finding is due to a bug in StepUp, so a debug build must not pass silently.
        # The report is an error (not a warning) to also get it into `FAIL_LOG`.
        reporter_handler.report("ERROR", description, pages)
        return ReturnCode.INTERNAL.value
    reporter_handler.report("WARNING", description, pages)
    return 0


#
# Signal handling
#


_DIRECTOR_KILL_GRACE = 15.0
"""Seconds the director gets to shut down after a terminal signal, before it is killed.

Comfortably longer than the director's own `INTERRUPT_GRACE`,
so that killing steps and reporting the outcome is tried first.
"""


@attrs.define
class TerminalSignalHandler:
    """Handle terminal signals received while the director subprocess is running.

    The director aborts the build on such a signal itself (see `DirectorHandler.interrupt`),
    so the work here is only to make sure it receives the signal,
    and to guarantee that the terminal user interface exits even when the director does not.
    """

    reporter_handler: ReporterHandler = attrs.field()
    """The reporter used to inform the user about the interruption."""

    process_director: asyncio.subprocess.Process = attrs.field()
    """The director subprocess, to forward signals to."""

    sig: signal.Signals | None = attrs.field(init=False, default=None)
    """The first terminal signal received, or `None` if the build was not interrupted."""

    _count: int = attrs.field(init=False, default=0)
    """The number of terminal signals received so far."""

    _kill_handle: asyncio.TimerHandle | None = attrs.field(init=False, default=None)
    """Handle of the scheduled `_kill_director` call, or `None` when none is pending."""

    def handle(self, sig: signal.Signals) -> None:
        """React to a terminal signal, escalating when it is received repeatedly."""
        self._count += 1
        if self._count == 1:
            self.sig = sig
            self.reporter_handler.report(
                "WARNING", f"Interrupted by {sig.name}. Waiting for the director to stop.", []
            )
        if self._count >= 3:
            self.reporter_handler.report("ERROR", "Killing the director (SIGKILL).", [])
            self._kill_director()
            return
        if not _terminal_broadcasts(sig, self.process_director.pid):
            # Only this process received the signal, so the director must be told separately.
            with contextlib.suppress(ProcessLookupError):
                self.process_director.send_signal(sig)
        self._arm_kill_timer()

    def cancel_kill(self) -> None:
        """Cancel the pending last-resort kill, e.g. because the director exited in time."""
        if self._kill_handle is not None:
            self._kill_handle.cancel()
            self._kill_handle = None

    def translate_wait_status(self, wait_status: int) -> int:
        """Turn the director's wait status into a `ReturnCode` flag combination.

        Both corrections applied here are about signals,
        which is why this lives on the signal handler:
        the director may have been killed by one, and this process may have received one.

        Parameters
        ----------
        wait_status
            What `Process.wait()` returned:
            the director's exit code, or minus the number of the signal that killed it.

        Returns
        -------
        returncode
            The director's exit code,
            with `ReturnCode.INTERRUPTED` OR-ed in when a terminal signal was received,
            and replaced by `ReturnCode.INTERNAL` when the director was killed by a signal,
            since a negative wait status is not a `ReturnCode` combination.
        """
        if wait_status < 0:
            signal_name = signal.Signals(-wait_status).name
            self.reporter_handler.report("ERROR", f"Director killed by {signal_name}", [])
            returncode = ReturnCode.INTERNAL.value
        else:
            returncode = wait_status
        if self.sig is not None:
            # Record that the build was aborted, on top of whatever the director reported.
            # Without this, an interrupted build is indistinguishable from a completed one.
            returncode |= ReturnCode.INTERRUPTED.value
        return returncode

    def _arm_kill_timer(self) -> None:
        """Schedule the last-resort kill, unless it is already scheduled.

        This guarantees the user gets the shell prompt back
        even when the director gets stuck on its way out.
        """
        if self._kill_handle is None:
            self._kill_handle = asyncio.get_running_loop().call_later(
                _DIRECTOR_KILL_GRACE, self._kill_director
            )

    def _kill_director(self) -> None:
        self.cancel_kill()
        with contextlib.suppress(ProcessLookupError):
            self.process_director.kill()


@attrs.define
class SuspendHandler:
    """Handle a `SIGTSTP` (Ctrl-Z) received while the director subprocess is running.

    A suspension is not an interruption:
    unlike `TerminalSignalHandler`, this leaves the return code alone
    and never gives up on the director.
    The director stops its own steps and itself (see `DirectorHandler.suspend`),
    so the work here is to hand the terminal back to the shell in a usable state
    and to take it back afterwards.
    """

    reporter_handler: ReporterHandler = attrs.field()
    """The reporter whose live display must be stopped for the duration of the suspension."""

    process_director: asyncio.subprocess.Process = attrs.field()
    """The director subprocess, to forward signals to."""

    raw_terminal: "RawTerminal | None" = attrs.field(default=None)
    """The terminal to hand back and take over again, or `None` when not interactive."""

    def handle(self) -> None:
        """Suspend this process, and resume when it is continued.

        The default disposition is restored to actually stop,
        since a handled `SIGTSTP` does not stop anything.
        Execution continues after `os.kill` once the shell continues the process group,
        which is where the terminal is taken over again.
        """
        self.reporter_handler.suspend_display()
        if self.raw_terminal is not None:
            self.raw_terminal.suspend()
        forwarded = not _terminal_broadcasts(signal.SIGTSTP, self.process_director.pid)
        if forwarded:
            # Only this process received the signal, so the director must be told separately.
            with contextlib.suppress(ProcessLookupError):
                self.process_director.send_signal(signal.SIGTSTP)
        loop = asyncio.get_running_loop()
        loop.remove_signal_handler(signal.SIGTSTP)
        wtime_start = time.perf_counter()
        try:
            os.kill(os.getpid(), signal.SIGTSTP)
        finally:
            suspended = time.perf_counter() - wtime_start
            loop.add_signal_handler(signal.SIGTSTP, self.handle)
            if forwarded:
                # The shell continues the process group of its job.
                # That group does not include a director
                # that never received the suspension from the terminal.
                with contextlib.suppress(ProcessLookupError):
                    self.process_director.send_signal(signal.SIGCONT)
            if self.raw_terminal is not None:
                self.raw_terminal.resume()
            self.reporter_handler.resume_display(suspended)


_TERMINAL_SIGNALS = frozenset({signal.SIGINT, signal.SIGTSTP})
"""The signals StepUp handles that the terminal driver can generate itself."""


def _terminal_broadcasts(sig: signal.Signals, pid: int) -> bool:
    """Check whether the terminal driver already sent `sig` to `pid` as well as to this process.

    Of the signals StepUp handles,
    only `SIGINT` (Ctrl-C) and `SIGTSTP` (Ctrl-Z) are generated by the terminal driver,
    which sends them to every process in the foreground process group at once.
    That is the case exactly when this process and `pid`
    are both in the process group that owns the controlling terminal.
    A `SIGINT` or `SIGTSTP` sent explicitly (`kill -INT`, `kill -TSTP`) to a backgrounded StepUp
    does not qualify, and neither does any `SIGTERM`.

    Parameters
    ----------
    sig
        The signal that was received by this process.
    pid
        The process id of the director.

    Returns
    -------
    broadcast
        `True` if `pid` received `sig` from the terminal driver as well.
    """
    if sig not in _TERMINAL_SIGNALS:
        return False
    try:
        # Query the controlling terminal itself:
        # stdin may be redirected while the process is still in the foreground process group.
        with open("/dev/tty") as tty:
            foreground_pgid = os.tcgetpgrp(tty.fileno())
        return foreground_pgid == os.getpgid(0) == os.getpgid(pid)
    except OSError:
        return False


#
# Keyboard
#


@attrs.define
class KeyAction:
    """A single keystroke's dispatch entry: RPC call, reported message, and help label."""

    method: str = attrs.field(kw_only=True)
    """The name of the `DirectorHandler` RPC method to call."""

    label: str = attrs.field(kw_only=True)
    """The short label for this key shown in `_KEY_STROKE_HELP`."""

    description: str = attrs.field(kw_only=True)
    """A longer description of what the key does."""

    message: str | None = attrs.field(kw_only=True, default=None)
    """The message reported to the terminal."""

    args: tuple = attrs.field(kw_only=True, default=())
    """Positional arguments passed to the RPC call."""


_KEY_ACTIONS = {
    "g": KeyAction(
        method="write_graph",
        label="graph",
        description="Write the workflow graph to graph.txt.",
        args=("graph",),
    ),
    "d": KeyAction(
        method="drain",
        label="drain",
        description="Drain the scheduler. (Leaves build phase.)",
        message="Draining the scheduler.",
    ),
    "j": KeyAction(
        method="wait_and_shutdown",
        label="join",
        description="Wait for all steps to complete before shutting down.",
        message="Waiting for all steps before shutdown.",
    ),
    "q": KeyAction(
        method="shutdown",
        label="shutdown",
        description="Shut down the system. (1st is graceful. 2nd kills steps.)",
        message="Shutting down.",
    ),
    "r": KeyAction(
        method="start_build_phase",
        label="rebuild",
        description="Restart the builder. (Leaves watch phase.)",
        message="Restarting the builder.",
    ),
}

_KEY_STROKE_HELP = "\n".join(
    f"  {key} = {action.label:<10s}  {action.description}" for key, action in _KEY_ACTIONS.items()
)


@attrs.define
class RawTerminal:
    """Owner of the terminal attributes of `fd` while keyboard interaction is active.

    Single keystrokes can only be read when canonical input and echo are disabled,
    which is a process-wide change to a terminal shared with the shell.
    This class owns the attributes it found,
    so that the same state can be handed back on the way out (`leave`)
    and for the duration of a suspension (`suspend`).
    """

    fd: int = attrs.field()
    """The file descriptor of the terminal to reconfigure."""

    _saved: list | None = attrs.field(init=False, default=None)
    """The attributes found by `enter`, or `None` when this terminal is not reconfigured."""

    def __enter__(self) -> Self:
        self.enter()
        return self

    def __exit__(self, exc_type, exc_value, tb) -> None:
        self.leave()

    def enter(self) -> None:
        """Disable canonical input and echo, remembering the attributes found."""
        self._saved = termios.tcgetattr(self.fd)
        self._apply_raw()

    def leave(self) -> None:
        """Restore the attributes found by `enter`, for good."""
        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
            self._saved = None

    def suspend(self) -> None:
        """Restore the attributes found by `enter`, to be undone by `resume`.

        The shell takes the terminal back while StepUp is suspended,
        and it must not find it in a mode meant for single keystrokes.
        """
        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)

    def resume(self) -> None:
        """Disable canonical input and echo again, after a `suspend`.

        This is not optional bookkeeping:
        shells do not reliably restore the attributes of a job they continue,
        so without this the keyboard interface silently stops working after a Ctrl-Z,
        with every keystroke echoed and swallowed by the line buffer.
        """
        if self._saved is not None:
            self._apply_raw()

    def _apply_raw(self) -> None:
        """Clear the `ICANON` and `ECHO` flags on the terminal."""
        tcattr = termios.tcgetattr(self.fd)
        tcattr[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, tcattr)


async def _iter_keystrokes(stop_event: asyncio.Event) -> AsyncGenerator[str, None]:
    """Yield keystrokes from stdin, one at a time, until `stop_event` is set.

    The terminal must already be in raw mode..

    Reads happen in a background thread because putting stdin in non-blocking mode
    (e.g. via `asyncio.StreamReader`) also affects stdout and stderr
    when they share the same underlying open file description as stdin,
    which breaks `print` and `rich.print` for large output.

    The background thread may still be blocked in `sys.stdin.read(1)` after this generator closes,
    since there is no portable way to interrupt a blocking read on stdin from another thread.
    It is daemonic and the process is about to exit anyway,
    so it consumes at most one keystroke typed between the end of the build and process exit.
    """
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def _stdin_loop():
        while True:
            ch = sys.stdin.read(1)
            # read() keeps returning an empty string when stdin is closed, without blocking.
            if ch == "":
                break
            loop.call_soon_threadsafe(queue.put_nowait, ch)

    threading.Thread(target=_stdin_loop, daemon=True).start()
    async for ch in stoppable_iterator(queue.get, stop_event):
        yield ch


async def keyboard(
    director_socket_path: Path,
    reporter_handler: ReporterHandler,
    stop_event: asyncio.Event,
) -> None:
    """Read keystrokes from the terminal and dispatch them as RPC calls to the director.

    Runs until `stop_event` is set.
    An unrecognized key is reported back to the user together with `_KEY_STROKE_HELP`,
    instead of being silently ignored.

    No keystroke may take the build down with it,
    so every failure of a dispatched call is reported and the loop continues.

    Parameters
    ----------
    director_socket_path
        The socket at which the director listens for instructions.
    reporter_handler
        The reporter used to inform the user which key was dispatched,
        that a key is unsupported, or that its call failed.
    stop_event
        Set to stop reading keystrokes.
    """
    async for ch in _iter_keystrokes(stop_event):
        action = _KEY_ACTIONS.get(ch)
        if action is None:
            pages = [("Keys", _KEY_STROKE_HELP)]
            reporter_handler.report("KEYBOARD", f"Unsupported key {ch}", pages)
            continue
        try:
            async with await AsyncRPCClient.socket(director_socket_path) as client:
                method = getattr(client.call, action.method)
                if action.message is not None:
                    reporter_handler.report("KEYBOARD", action.message, [])
                await method(*action.args)
        except OSError as exc:
            # The director is on its way out (socket unlinked, or listening backlog gone),
            # or the connection dropped mid-call.
            # Losing a keystroke at that point is expected,
            # so report it and keep reading until stop_event is set.
            reporter_handler.report(
                "KEYBOARD", f"Director unreachable, key {ch} ignored: {exc}", []
            )
        except UsageError as exc:
            # The director rejected the call itself, e.g. because of an invalid argument.
            # Such an error carries a one-line message and no traceback,
            # so it is reported under a "Message" heading instead of the "Traceback" below.
            reporter_handler.report(
                "ERROR", f"Key {ch} failed in the director.", [("Message", str(exc).strip())]
            )
        except RPCError as exc:
            # The call did reach the director, but raised there,
            # e.g. `graph` could not write its output files.
            # Unlike an unreachable director, this is a real error worth landing in the fail log,
            # but it is still only this keystroke that failed.
            reporter_handler.report(
                "ERROR", f"Key {ch} failed in the director.", [("Traceback", str(exc).strip())]
            )
