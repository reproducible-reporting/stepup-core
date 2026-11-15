# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Command line of the director process, which sets up logging and the reporter."""

import argparse
import asyncio
import contextlib
import logging
import multiprocessing
import os
import sys
import traceback
from decimal import Decimal
from importlib.metadata import version as get_version

from ..constants import (
    DIRECTOR_LOG_PID_PREFIX,
    DIRECTOR_LOG_SOCKET_PREFIX,
    DIRECTOR_PROF,
    DIRECTOR_SOCKET_SENTINEL,
    GRAPH_DB,
    JOBLOG_CSV,
    SQLLOG_CSV,
    SQLLOG_JSON,
)
from ..graph.sqlite3 import DBSession, SQLLog
from ..path import Path
from ..platform.asyncio import tolerate_proactor_connection_errors
from ..platform.stdio import write_utf8_unless_terminal
from ..rpc.transport import RPC_TRANSPORTS
from ..utils import positive_int
from .reporter import ReporterClient
from .serve import ServeConfig, interpret_jobs, serve

try:
    import yappi
except ImportError:
    yappi = None

__all__ = ()

logger = logging.getLogger(__name__)


def main():
    write_utf8_unless_terminal()
    args = parse_args()
    mp_ctx = None
    if args.forkserver:
        mp_ctx = multiprocessing.get_context("forkserver")
        preload = ["stepup.core.director.executor"]
        if args.preload_modules:
            preload.extend(m.strip() for m in args.preload_modules.split(",") if m.strip())
        mp_ctx.set_forkserver_preload(preload)
    with contextlib.ExitStack() as stack:
        sqllog = (
            stack.enter_context(SQLLog(path_queries=SQLLOG_JSON, path_timings=SQLLOG_CSV))
            if args.sqllog
            else None
        )
        db = stack.enter_context(DBSession.open(GRAPH_DB, sqllog=sqllog))
        returncode = asyncio.run(async_main(args, db, mp_ctx))
    sys.exit(returncode)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stepup-director",
        description="Launch the director.",
    )
    parser.add_argument(
        "director_socket",
        type=Path,
        help="The path at which StepUp will listen for instructions: "
        "the Unix domain socket or the endpoint file of the TCP socket, see --rpc-transport.",
    )
    parser.add_argument(
        "--cgroup",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Enable peak memory usage tracking through cgroups.",
    )
    parser.add_argument(
        "--clean",
        dest="clean",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Remove outdated output files.",
    )
    parser.add_argument(
        "--duration",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Use the duration of steps to optimize the execution order.",
    )
    parser.add_argument(
        "--explain-rerun",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Explain for every step with recorded info why it cannot be skipped.",
    )
    parser.add_argument(
        "--keep-going",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Keep dispatching new steps after another step has failed, "
        "instead of draining the scheduler. (In-progress steps always finish.)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level. [default=%(default)s]",
    )
    parser.add_argument(
        "--jobs",
        type=Decimal,
        default=Decimal("1.0"),
        help="Number of jobs running in parallel. "
        "When given as a real number with digits after the decimal point, "
        "it is multiplied with the number of available cores. [default=%(default)s]",
    )
    parser.add_argument(
        "--joblog",
        default=False,
        action=argparse.BooleanOptionalAction,
        help=f"Record job-execution events (init, created, started, ended, completed) to "
        f"{JOBLOG_CSV}, for diagnosing scheduler/executor dispatch overhead.",
    )
    parser.add_argument(
        "--fix-epoch",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Set the SOURCE_DATE_EPOCH environment variable to 315532800. "
        "This corresponds to 1980-01-01 00:00:00 UTC. "
        "(If the variable is already set, it will be used as-is.)",
    )
    parser.add_argument(
        "--forkserver",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Use a forkserver for Python step execution "
        "to reduce startup overhead. [default=%(default)s]",
    )
    parser.add_argument(
        "--preload-modules",
        default=None,
        help="Comma-separated list of Python modules to pre-load into the forkserver. "
        "Only has effect when --forkserver is active.",
    )
    parser.add_argument(
        "--live-progress",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Send live step-count updates to the reporter. Set by the TUI when its "
        "console is an interactive terminal. [default=%(default)s]",
    )
    parser.add_argument(
        "--reporter",
        type=Path,
        dest="reporter_socket",
        default=os.environ.get("STEPUP_REPORTER_SOCKET"),
        help="Socket to send reporter updates to, if any.",
    )
    parser.add_argument(
        "--rpc-transport",
        required=True,
        choices=RPC_TRANSPORTS,
        help="The transport on which the director listens for instructions. "
        "The reporter socket reveals its own transport.",
    )
    parser.add_argument(
        "--defer-cap",
        type=positive_int,
        default=100,
        help="Maximum number of consecutive defers (since the last success) before "
        "a step is failed instead of parked. A livelock guard. [default=%(default)s]",
    )
    parser.add_argument(
        "--resources",
        default=None,
        help="Available resources for steps, e.g. 'cpu:4,gpu:1,memgb:16'.",
    )
    parser.add_argument(
        "--sqllog",
        default=False,
        action=argparse.BooleanOptionalAction,
        help=f"Enable SQLite debug logging: append per-query timing rows to {SQLLOG_CSV} "
        f"as they execute, and write a query/call-site/plan index to {SQLLOG_JSON} "
        "when the director exits.",
    )
    parser.add_argument(
        "--target",
        dest="targets",
        action="append",
        default=[],
        type=Path,
        help="Restrict the build to steps needed to produce this output file. "
        "May be repeated. When omitted, the full default workflow is built.",
    )
    parser.add_argument(
        "--target-dir",
        dest="target_dirs",
        action="append",
        default=[],
        type=Path,
        help="Restrict the build to declared-DEFAULT steps whose output falls under this "
        "directory (trailing slash included). May be repeated. Director-internal: the "
        "TUI classifies raw CLI targets into --target/--target-dir; see cli/build.py.",
    )
    parser.add_argument(
        "--watch",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Watch file changes after completing the build phase. "
        "When not given, the director exits after completing the build phase.",
    )
    parser.add_argument(
        "--watch-first",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Exit watch phase and start the builder after the first file change. "
        "This implies --watch.",
    )
    parser.add_argument(
        "--yappi",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Profile the director with Yappi (must be installed).",
    )
    args = parser.parse_args()
    if args.watch_first:
        args.watch = True
    return args


async def async_main(
    args: argparse.Namespace,
    db: DBSession,
    mp_ctx: multiprocessing.context.BaseContext | None = None,
) -> int:
    """Set up logging and the reporter, then run `serve()` and report its outcome.

    Returns
    -------
    returncode
        The exit code of the director process.
    """
    tolerate_proactor_connection_errors()
    logging.basicConfig(
        format="%(asctime)s  %(levelname)8s  %(name)24s  ::  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=args.log_level,
    )
    print(f"{DIRECTOR_LOG_SOCKET_PREFIX}{args.director_socket}", file=sys.stderr)
    print(f"{DIRECTOR_LOG_PID_PREFIX}{os.getpid()}", file=sys.stderr)
    print(f"LOG_LEVEL {args.log_level}", file=sys.stderr)
    # To detect invalid usage of the RPC client of stepup.core.api within the director process,
    # we set STEPUP_DIRECTOR_SOCKET to an invalid value.
    os.environ["STEPUP_DIRECTOR_SOCKET"] = DIRECTOR_SOCKET_SENTINEL
    if args.yappi:
        if yappi is None:
            print(
                "Yappi profiling requested, but the yappi module is not installed.",
                file=sys.stderr,
            )
        else:
            yappi.set_clock_type("cpu")
            yappi.start(builtins=True, profile_threads=True)
    async with ReporterClient.socket(args.reporter_socket) as reporter:
        njob = interpret_jobs(args.jobs)
        await reporter.set_njob(njob)
        version = get_version("stepup")
        await reporter("DIRECTOR", f"Listening on {args.director_socket} (StepUp Core {version})")
        serve_result = None
        try:
            serve_result = await serve(
                ServeConfig.from_args(args, njob),
                director_socket_path=args.director_socket,
                rpc_transport=args.rpc_transport,
                reporter=reporter,
                db=db,
                mp_ctx=mp_ctx,
            )
        except Exception as exc:
            tbstr = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            pages = [("Traceback", tbstr.strip())]
            await reporter("ERROR", "The director raised an exception.", pages)
            raise
        finally:
            if serve_result is not None and len(serve_result.usage_summary) > 0:
                await reporter("DIRECTOR", serve_result.usage_summary)
            await reporter("DIRECTOR", "See you!")
            await reporter.stop_reporting()
            if args.yappi and yappi is not None:
                yappi.stop()
                stats = yappi.get_func_stats()
                stats.save(DIRECTOR_PROF, type="pstat")
            if serve_result is not None:
                print(serve_result.usage_report, file=sys.stderr)
    return serve_result.returncode.value


if __name__ == "__main__":
    sys.exit(main())
