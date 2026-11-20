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
import functools
import signal
import subprocess
import sys
import tempfile
from decimal import Decimal

from ..constants import (
    DIRECTOR_LOG,
    JOBLOG_CSV,
    PERF_DATA,
    PLAN_PY,
    SQLLOG_CSV,
    SQLLOG_JSON,
    STEPUP_DIR,
)
from ..enums import ReturnCode
from ..exceptions import ConfigError, ToolError
from ..path import Path, get_stepup_root, to_slash
from ..platform.asyncio import (
    signal_handlers,
    tolerate_proactor_connection_errors,
    wait_for_path,
)
from ..platform.cgroups import cgroup_scope_prefix
from ..platform.oscompat import (
    abort_signals,
    forkserver_available,
    perf_available,
    raw_terminal_available,
    suspend_available,
    windows_console_available,
)
from ..rpc.server import SocketRPCServer
from ..rpc.transport import select_rpc_transport
from ..utils import is_debug, merge_resources, positive_decimal, positive_int
from ..watch.select import select_watcher_backend
from .config_loader import ConfigLoader
from .director_log import is_process_running, query_director_log, scan_director_log
from .reporter import ReporterHandler
from .terminal import (
    RawTerminal,
    SuspendHandler,
    TerminalSignalHandler,
    keyboard,
    stdin_has_keystrokes,
)
from .tool import SubParsers, ToolFunc

if windows_console_available():
    from ..platform.winapi import JobObject

__all__ = (
    "add_boot_subcommand",
    "add_build_subcommand",
)


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


def _add_build_parser(
    subparsers: SubParsers, loader: ConfigLoader, name: str, help_text: str
) -> None:
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
        "-e",
        "--explain-rerun",
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
        "-j",
        "--jobs",
        type=positive_decimal,
        default=Decimal("1.0"),
        help="Number of jobs running in parallel. "
        "When given as a real number with digits after the decimal point, "
        "it is multiplied with the number of available cores. [default=%(default)s]",
    )
    group.add_argument(
        "-k",
        "--keep-going",
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
        "--offline",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Work without access to remote data. "
        "STEPUP_DIRECTOR_I keeps the value of the previous start, "
        "so steps that run at every start keep their results. "
        "Steps that run anyway find STEPUP_BUILD_OFFLINE=1 in their environment.",
    )
    group.add_argument(
        "--progress",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Report progress information in the terminal user interface. "
        "(This can be useful to simplify and reduce the output.)",
    )
    group.add_argument(
        "-r",
        "--resources",
        default=None,
        action=MergeResourcesAction,
        help="Available resources for steps, e.g. 'cpu:4,gpu:1,memgb:16'. "
        "Merged with (not overriding) config files, the STEPUP_BUILD_RESOURCES env var, "
        "and any earlier --resources on the command line.",
    )
    group.add_argument(
        "-w",
        "--watch",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="StepUp will watch for file changes after all runnable steps have been executed. "
        "By pressing r, it will rerun steps that have become pending due to the file changes.",
    )
    group.add_argument(
        "-W",
        "--watch-first",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Start the builder after observing the first file change in watch mode. "
        "This implies --watch.",
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
        help="Use a forkserver for Python step execution "
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
        "-l",
        "--log-level",
        default="DEBUG" if is_debug() else "WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help=f"Set the level of the messages that the director writes to {DIRECTOR_LOG}. "
        "[default=%(default)s]",
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


def add_build_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
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


def add_boot_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
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
# Tool functions
#


def _build_tool(args: argparse.Namespace) -> None:
    """Run `stepup build` and exit with the return code that the build produced.

    Parameters
    ----------
    args
        The parsed `stepup build` command-line arguments.
    """
    sys.exit(asyncio.run(_async_build(args)))


def _deprecated_boot_tool(args: argparse.Namespace) -> None:
    """Run `stepup boot`, the deprecated alias of `stepup build`, after printing a warning.

    Parameters
    ----------
    args
        The parsed `stepup boot` command-line arguments.
    """
    print(
        "Warning: 'stepup boot' is deprecated; use 'stepup build' instead.",
        file=sys.stderr,
    )
    _build_tool(args)


#
# The build driver
#


async def _async_build(args: argparse.Namespace) -> int:
    """Launch the director, forward keystrokes and terminal signals to it, and report progress.

    The socket paths, see `stepup.core.rpc.transport`, are placed in a per-run temporary directory
    rather than in `${PWD}/.stepup/`, for two reasons.
    Only its owner can access it, which keeps other users out of the RPC connections.
    Unix domain socket paths have a strict limit of 104 bytes (macOS) / 108 bytes (Linux),
    which deeply nested project paths can easily breach.

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
    ConfigError
        If watch mode is requested with an unusable file watcher (see `select_watcher_backend`),
        if the RPC transport cannot be used (see `select_rpc_transport`),
        or if a requested feature is not available on this platform
        (see `_check_platform_support`).
    """
    tolerate_proactor_connection_errors()
    _check_platform_support(args)
    rpc_transport = select_rpc_transport()
    stepup_root = get_stepup_root()
    targets, target_dirs = _normalize_targets(args.targets, stepup_root)
    if stepup_root != Path.cwd():
        print("Changing to", stepup_root)
        stepup_root.cd()

    # Sanity check before creating a subdirectory.
    if not PLAN_PY.is_file():
        raise ToolError("File plan.py does not exist.")
    if args.watch or args.watch_first:
        # The director selects the file watcher itself,
        # but a bad choice is reported here as a usage error instead of a crash of the director.
        select_watcher_backend()

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
            SocketRPCServer(reporter_handler, reporter_socket_path, rpc_transport).serve(
                stop_event
            ),
            name="reporter-rpc",
        )
        try:
            argv = _build_director_argv(
                args,
                targets,
                target_dirs,
                director_socket_path,
                reporter_socket_path,
                rpc_transport=rpc_transport,
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


def _check_platform_support(args: argparse.Namespace) -> None:
    """Refuse options that enable a feature this platform does not have.

    Parameters
    ----------
    args
        The parsed `stepup build` command-line arguments,
        including the defaults taken from the configuration files and environment variables.

    Raises
    ------
    ConfigError
        If the forkserver or `perf` is requested on a platform without it.
    """
    if args.forkserver and not forkserver_available():
        raise ConfigError(
            f"The forkserver is not available on {sys.platform}. "
            "Do not enable it with --forkserver, STEPUP_BUILD_FORKSERVER or a config file."
        )
    if args.perf is not None and not perf_available():
        raise ConfigError(
            f"Profiling with perf is only supported on Linux, not on {sys.platform}. "
            "Do not enable it with --perf, STEPUP_BUILD_PERF or a config file."
        )


def _normalize_targets(raw_targets: list[str], stepup_root: Path) -> tuple[list[Path], list[Path]]:
    """Resolve and validate `stepup build` target arguments against the project root.

    A raw target ending in a path separator is a directory target:
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
        raw_target = to_slash(raw_target)
        is_dir_target = raw_target.endswith("/")
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
    """Refuse to start next to a running director, then clear the log files in `.stepup`.

    The order matters and is the reason these two steps live in one function:
    the running-director check reads `DIRECTOR_LOG`, which is one of the files cleared.
    Checking second would make the check unconditionally pass.

    `DIRECTOR_LOG` is emptied instead of removed,
    because other `stepup` commands read it while they wait for the director,
    e.g. `stepup wait` right after `stepup build &`.
    Windows does not remove a file that another process has open,
    but it does let it be emptied.

    Raises
    ------
    ToolError
        If another director may still be running (see `_check_no_running_director`).
    """
    _check_no_running_director()
    STEPUP_DIR.makedirs_p()
    for path_log in STEPUP_DIR.glob("*.log"):
        if path_log.name == DIRECTOR_LOG.name:
            with open(path_log, "w", encoding="utf-8", newline="\n"):
                pass
        else:
            path_log.remove_p()


def _build_director_argv(
    args: argparse.Namespace,
    targets: list[Path],
    target_dirs: list[Path],
    director_socket_path: Path,
    reporter_socket_path: Path,
    *,
    rpc_transport: str,
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
    rpc_transport
        The transport on which the director will listen, see `select_rpc_transport`.
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
            f"--rpc-transport={rpc_transport}",
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
    if args.offline:
        argv.append("--offline")
    if live_progress:
        argv.append("--live-progress")
    if args.resources:
        argv.append(f"--resources={args.resources}")
    if args.sqllog:
        argv.append("--sqllog")
    if args.joblog:
        argv.append("--joblog")
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
    director_job = None
    try:
        with open(DIRECTOR_LOG, "w", encoding="utf-8", newline="\n") as log_file:
            # The child gets a duplicate of the log file descriptor,
            # so this handle can be closed right after the spawn,
            # instead of being kept open for the whole build.
            process_director = await asyncio.create_subprocess_exec(
                *argv,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        director_job = _confine_director(process_director.pid)
        # Handle terminal signals, to avoid internal tracebacks
        # when the user presses Ctrl-C or the process is asked to stop, e.g. by a SIGTERM.
        # The director aborts the build itself,
        # so this waits for it rather than exiting straight away,
        # which would cut its shutdown short.
        signal_handler = TerminalSignalHandler(reporter_handler, process_director)
        callbacks = {sig: functools.partial(signal_handler.handle, sig) for sig in abort_signals()}
        suspend_handler = None
        if suspend_available():
            suspend_handler = SuspendHandler(reporter_handler, process_director)
            callbacks[signal.SIGTSTP] = suspend_handler.handle
        # Wait for the director in a task,
        # so that the interactive setup below can race the appearance of its socket
        # against its exit.
        task_director = asyncio.create_task(process_director.wait(), name="director-wait")
        try:
            with signal_handlers(callbacks):
                # Set up keyboard interaction, or work non-interactively.
                if stdin_has_keystrokes() and await _wait_for_director_socket(
                    director_socket_path, stop_event, task_director
                ):
                    if raw_terminal_available():
                        # Raw mode is entered here, not inside the keyboard task,
                        # so that it is already in place when that task starts,
                        # and so that the suspend handler has a terminal
                        # to hand back to the shell.
                        raw_terminal = RawTerminal(sys.stdin.fileno())
                        raw_terminal.enter()
                        if suspend_handler is not None:
                            suspend_handler.raw_terminal = raw_terminal
                    task_keyboard = asyncio.create_task(
                        keyboard(director_socket_path, reporter_handler, stop_event),
                        name="keyboard",
                    )
                wait_status = await task_director
        finally:
            signal_handler.cancel_kill()
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
        if director_job is not None:
            director_job.close()


def _confine_director(pid: int) -> "JobObject | None":
    """Make sure that the director cannot outlive this process, where the platform needs it.

    A POSIX terminal that goes away hangs up this process and the director alike.
    A Windows console does not:
    when the SSH session of a build was closed, on Windows 11 24H2 with OpenSSH,
    this process ended at once, without a signal, and the director carried on without it.
    The director is therefore put in a Job Object that only this process holds,
    so that Windows ends the director, and with it the steps, when this process is gone.

    Parameters
    ----------
    pid
        The process id of the director, which must still be running or waited for by the caller.

    Returns
    -------
    job
        The job to close when the director has exited,
        or `None` on other platforms than Windows and when Windows refuses.
    """
    if not windows_console_available():
        return None
    try:
        job = JobObject.create()
    except OSError:
        return None
    try:
        job.assign(pid)
    except OSError:
        job.close()
        return None
    return job


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
        The socket path, see `stepup.core.rpc.transport`,
        at which something appears once the director accepts connections.
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
    if task_socket.done():
        return task_socket.result()
    # The director exited first, which does not rule out that it created its socket before.
    task_socket.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task_socket
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
    if is_debug():
        # Every finding is due to a bug in StepUp, so a debug build must not pass silently.
        # The report is an error (not a warning) to also get it into `FAIL_LOG`.
        reporter_handler.report("ERROR", description, pages)
        return ReturnCode.INTERNAL.value
    reporter_handler.report("WARNING", description, pages)
    return 0
