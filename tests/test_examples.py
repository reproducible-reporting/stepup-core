# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run examples."""

import asyncio
import contextlib
import functools
import os
import re
import shutil
import signal
import sys
from collections.abc import Callable

import pytest

from stepup.core.constants import DIRECTOR_LOG
from stepup.core.path import Path
from stepup.core.pytest import EXAMPLE_TIMEOUT, run_example, run_plan
from stepup.core.utils import is_process_running, query_director_log

pytestmark = pytest.mark.timeout(2 * EXAMPLE_TIMEOUT)
"""Budget for a whole example, which `run_example` already bounds with `EXAMPLE_TIMEOUT`,
plus twice `EXAMPLE_STOP_GRACE` to stop what the example left running.

Setup, call and teardown share this budget,
and it must stay above the deadline of the example itself,
so that a stalled example is reported with its own output instead of a bare timeout.
"""

OVERWRITE_EXPECTED = "STEPUP_OVERWRITE_EXPECTED" in os.environ

EXAMPLES = [
    "absolute",
    "amend",
    "amend_already_declared",
    "amend_chdir",
    "amend_delete_both",
    "amend_env_vars",
    "amend_env_vars_subs",
    "amend_missing_inp",
    "amend_missing_out",
    "amend_missing_vol",
    "amend_mixed",
    "amend_restart_restore",
    "amend_restart_skip",
    "amend_self_static",
    "amend_self_static_change",
    "amend_self_static_replay",
    "amend_self_static_restart",
    "amend_static",
    "amend_validate1",
    "amend_validate2",
    "amend_watch_restore",
    "build_target",
    "build_target_dir",
    "build_target_dir_mixed",
    "build_target_dir_resume",
    "build_target_invalid",
    "build_target_resume",
    "build_target_stale_decl",
    "build_target_stale_target",
    "build_target_static_resume",
    "call_args_file_json",
    "call_args_file_no_ext",
    "call_args_file_workdir",
    "call_args_file_yaml",
    "call_chain",
    "call_conditional",
    "call_env",
    "call_fan_out",
    "call_fn_plan",
    "call_inp_known",
    "call_kwargs",
    "call_list",
    "call_minimal",
    "call_out_known",
    "call_plan_amends",
    "call_resources",
    "call_restart_change_args_file",
    "call_restart_change_inp",
    "call_restart_change_kwargs",
    "call_run_amends",
    "call_vol",
    "call_whitespace",
    "call_workdir",
    "clean_after",
    "clean_changed",
    "clean_dir",
    "clean_dir_orphan",
    "clean_dir_stale",
    "clean_dir_static",
    "clean_dir_volatile",
    "clean_dir_workdir",
    "clean_drained_scheduler",
    "clean_nonexisting",
    "clean_detached",
    "clean_planned1",
    "clean_planned2",
    "command_callable",
    "config_sections",
    "copy_mkdir",
    "cyclic_dynamic",
    "cyclic_static",
    "detach_running_skip",
    "detach_running_step",
    "drop_dynamic",
    "dynamic_module",
    "env_overrides",
    "env_vars",
    "env_vars_config_add",
    "env_vars_config_touch",
    "env_vars_multi",
    "env_vars_path",
    "env_vars_restart_set",
    "env_vars_restart_unset",
    "env_vars_restart_value",
    "env_vars_subs",
    "env_vars_workdir",
    "error_amend_while_holding",
    "error_cyclic_late",
    "error_debug_traceback",
    "error_detach_running",
    "error_detach_running_amend",
    "error_env_var",
    "error_main_fails",
    "error_output_becomes_dir",
    "error_plan_no_shebang",
    "error_plan_not_executable",
    "error_runpy_no_shebang",
    "error_runpy_not_executable",
    "error_runsh_no_shebang",
    "error_runsh_not_executable",
    "error_runsh_opts",
    "error_short_traceback",
    "error_step",
    "error_stepup_dir",
    "external_sources",
    "filter_deps_symlink",
    "get_info",
    "glob_dir_outside_tree",
    "glob_dir_parent_of_static",
    "glob_justified_late",
    "glob_no_declare",
    "glob_output_after",
    "glob_output_before",
    "glob_overlapping",
    "glob_stepup_dir",
    "glob_unjustified_file",
    "glob_unjustified_skipped_step",
    "glob_unjustified_watch",
    "glob_volatile",
    "here",
    "hold_nested",
    "hold_orders_by_duration",
    "hold_skip_bypass",
    "inp_digest",
    "input_check_changed",
    "input_check_deleted",
    "input_check_defer",
    "input_check_three",
    "input_check_two",
    "keep_going",
    "no_clean",
    "no_clean_planned_detached",
    "no_data",
    "no_plan",
    "no_shebang",
    "no_static",
    "not_cyclic",
    "not_cyclic_change",
    "not_cyclic_replay",
    "not_cyclic_restart",
    "optional_chain",
    "optional_change",
    "optional_change_sub1",
    "optional_change_sub2",
    "optional_cleanup",
    "optional_convert",
    "optional_convert_reselect",
    "optional_convert_restore",
    "optional_cross_plan",
    "optional_dynamic",
    "optional_dynamic_twice",
    "optional_recreate_deep_sink",
    "optional_recreate_stale_sink",
    "optional_volatile",
    "output_not_created",
    "output_storage",
    "path_filter",
    "pending",
    "pending_after_failure",
    "pending_cleanup",
    "pending_nested",
    "pending_shutdown",
    "permissions_file_rerun",
    "permissions_plan_rerun",
    "permissions_plan_restart",
    "permissions_step_rerun",
    "permissions_step_restart",
    "plan_inp",
    "preload_modules",
    "record_subprocess",
    "record_subprocess_shell",
    "recreate_elsewhere",
    "recursive_glob",
    "recycle_running",
    "relax_amend_liveness",
    "relax_amend_restart",
    "relax_amend_success",
    "runpyep",
    "render_jinja_dict",
    "render_jinja_fail",
    "render_jinja_json",
    "render_jinja_python",
    "render_jinja_relpath",
    "render_jinja_toml",
    "render_jinja_yaml",
    "replay_nglob",
    "replay_static",
    "replay_static_tree",
    "replay_static_tree_plan",
    "reqdir_missing",
    "defer_cap",
    "defer_cap_swallowed_error",
    "resource_multi",
    "resource_pool",
    "resource_skip",
    "resource_zero",
    "restart_add_missing",
    "restart_add_nglob",
    "restart_amend_concerted",
    "restart_blocked",
    "restart_changes",
    "restart_delete_nglob",
    "restart_detached",
    "restart_detached_self_static",
    "restart_failed",
    "restart_fix_bug",
    "restart_glob_first_match",
    "restart_input",
    "restart_mandatory",
    "restart_mode_change",
    "restart_nested",
    "restart_nochanges",
    "restart_shell_change",
    "restart_outdated_amend",
    "restart_output",
    "restart_queued",
    "restart_static_remove1",
    "restart_static_remove2",
    "restart_static_tree",
    "restart_static_tree_file_handover",
    "restart_static_tree_removed",
    "restart_static_tree_stale_build",
    "restart_step_changed",
    "script_cases",
    "script_cases_run_import",
    "script_cases_settings",
    "script_inp",
    "script_single",
    "script_single_optional",
    "script_single_run_import",
    "static",
    "static_abs",
    "static_after",
    "static_from_glob",
    "static_glob",
    "static_dirhash",
    "static_nglob",
    "static_nglob_recursive",
    "static_nglob_subdir",
    "static_pattern",
    "static_pattern_dir",
    "static_pattern_empty",
    "static_pattern_named",
    "static_pattern_overlap",
    "static_pattern_recursive_middle",
    "static_pattern_recursive_rejected",
    "static_pattern_restart_add",
    "static_pattern_restart_delete",
    "static_pattern_tree_overlap",
    "static_tree1",
    "static_tree2",
    "static_tree_absolute",
    "static_tree_amend",
    "static_tree_dirhash",
    "static_tree_file_after",
    "static_tree_file_handover",
    "static_tree_glob",
    "static_tree_glob_dir",
    "static_tree_glob_no_collision",
    "static_tree_nonexisting",
    "static_tree_root",
    "static_tree_step",
    "static_tree_sub",
    "static_tree_two_plans_file_first",
    "static_tree_two_plans_tree_first",
    "subdir",
    "subdir_import",
    "subprocess_output",
    "supply_detached_output",
    "translate_external",
    "undeclared_detached",
    "wait_first",
    "watch_add_missing",
    "watch_dynamic_change",
    "watch_dynamic_delete",
    "watch_dynamic_restore",
    "watch_blocked",
    "watch_boot",
    "watch_chain",
    "watch_failed",
    "watch_glob_delete",
    "watch_glob_delete_dir",
    "watch_glob_first_match",
    "watch_input",
    "watch_mandatory",
    "watch_middle",
    "watch_mixed",
    "watch_missing_dir",
    "watch_mode_change",
    "watch_move_dir_simple",
    "watch_nochanges",
    "watch_outdated_amend1",
    "watch_outdated_amend2",
    "watch_output",
    "watch_static_remove1",
    "watch_static_remove2",
    "watch_static_tree_glob",
    "watch_undeclared",
    "watch_volatile",
    "watch_wanted",
    "warn_plan_non_plan",
    "whitespace",
    "workdir",
]

# Examples that can only run when StepUp uses the forkserver path.
EXAMPLES_REQUIRES_FORKSERVER = {"preload_modules"}

# Examples that change the permission bits of a file, or that need a file without executable bit.
EXAMPLES_REQUIRE_FILE_MODES = {
    # The plan has no executable bit, which the director refuses.
    "error_plan_not_executable",
    # The script of a step has no executable bit, which fails the step.
    "error_runpy_not_executable",
    "error_runsh_not_executable",
    # A step reruns after `chmod` of one of its inputs.
    "permissions_file_rerun",
    "restart_mode_change",
    "watch_mode_change",
    # A plan or a step fails until `chmod +x` of its script.
    "permissions_plan_rerun",
    "permissions_plan_restart",
    "permissions_step_rerun",
    "permissions_step_restart",
}

# Examples in which `main.sh` creates a symbolic link.
EXAMPLES_REQUIRE_SYMLINKS = {"filter_deps_symlink"}

# Examples that declare `/usr/bin/env` as a static file.
EXAMPLES_REQUIRE_USR_BIN_ENV = {"static_abs"}

EXAMPLE_MARKERS = {
    "requires_forkserver": EXAMPLES_REQUIRES_FORKSERVER,
    "requires_file_modes": EXAMPLES_REQUIRE_FILE_MODES,
    "requires_symlinks": EXAMPLES_REQUIRE_SYMLINKS,
    "requires_usr_bin_env": EXAMPLES_REQUIRE_USR_BIN_ENV,
}
"""The examples that are skipped without a capability, by the marker of that capability.

The markers are defined by `CAPABILITY_MARKERS` in `conftest.py`.
"""


def _example_param(name: str):
    """Give an example the markers of the capabilities that it needs."""
    marks = [
        getattr(pytest.mark, marker) for marker, names in EXAMPLE_MARKERS.items() if name in names
    ]
    return pytest.param(name, marks=marks) if marks else name


@pytest.mark.parametrize("name", [_example_param(name) for name in EXAMPLES])
async def test_example(name: str, path_tmp: Path):
    await run_example(Path("tests/examples") / name, path_tmp, OVERWRITE_EXPECTED)


def test_examples_list_has_all_dirs():
    """Every directory under `tests/examples` is listed in `EXAMPLES`."""
    dirs = {path.name for path in Path("tests/examples").dirs()}
    missing = dirs - set(EXAMPLES)
    assert not missing, f"Example directories not listed in EXAMPLES: {sorted(missing)}"


def test_example_markers_name_examples():
    """Every example that is given a capability marker is listed in `EXAMPLES`."""
    marked = set().union(*EXAMPLE_MARKERS.values())
    unknown = marked - set(EXAMPLES)
    assert not unknown, f"Marked examples that are not listed in EXAMPLES: {sorted(unknown)}"


def test_examples_list_has_no_extra():
    """Every entry in `EXAMPLES` corresponds to a directory under `tests/examples`."""
    dirs = {path.name for path in Path("tests/examples").dirs()}
    extra = set(EXAMPLES) - dirs
    assert not extra, f"EXAMPLES entries without a directory: {sorted(extra)}"


@pytest.mark.parametrize(
    "name",
    [
        "amend",
        "command_callable",
        "not_cyclic",
        "optional_convert",
        "script_single",
        "static",
        "static_pattern",
    ],
)
async def test_plan(name: str, path_tmp: Path):
    await run_plan(Path("tests/examples") / name, path_tmp)


#
# Processes left behind by an example
#


HARNESS_PLAN = """\
#!/usr/bin/env python3
from stepup.core.api import run, static

static("step.py")
run("./step.py")
"""
"""A plan whose only step records its pid and then runs until it is stopped."""

HARNESS_STEP = """\
#!/usr/bin/env python3
import os
import time

with open("step.tmp", "w", encoding="utf-8") as fh:
    fh.write(str(os.getpid()))
os.replace("step.tmp", "step.pid")
time.sleep(600)
"""
"""A step that only the director can stop, and only when it gets the chance.

On POSIX, it runs in a session of its own.
It is a Python script because its pid must be one of the operating system:
a shell of Git for Windows reports the pid that the MSYS2 runtime gave it instead.
"""

HARNESS_MAIN_FAILS = """\
#!/usr/bin/env -S bash -x
source ../example.rc
echo $$ > main.pid
sb -j 1 -w &
echo $! > sb.pid
until [[ -f step.pid ]]; do sleep 0.1; done
exit 1
"""
"""A `main.sh` that fails while `stepup build` and its step are still running."""

HARNESS_MAIN_HANGS = """\
#!/usr/bin/env -S bash -x
source ../example.rc
echo $$ > main.pid
sb -j 1 -w &
echo $! > sb.pid
stepup wait
"""
"""A `main.sh` that blocks for as long as its step runs."""

PROCESS_EXIT_WAIT = 5.0
"""Seconds that a stopped process gets to disappear before it counts as left behind."""


def write_harness_example(path_tmp: Path, main: str) -> Path:
    """Write an example outside `tests/examples`, to test `run_example` itself.

    Parameters
    ----------
    path_tmp
        The temporary directory in which the example is written.
    main
        The contents of `main.sh`.

    Returns
    -------
    srcdir
        The source directory of the example, to be passed to `run_example`.
    """
    srcdir = path_tmp / "src" / "example"
    srcdir.makedirs_p()
    shutil.copy("tests/examples/example.rc", srcdir.parent / "example.rc")
    for name, text in ("main.sh", main), ("plan.py", HARNESS_PLAN), ("step.py", HARNESS_STEP):
        path = srcdir / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
    (srcdir / ".gitignore").write_text(".stepup/\n*.pid\n", encoding="utf-8")
    return srcdir


def _signal_reaches(send: Callable[[int, int], None], pid: int, sig: int) -> bool:
    """Send a signal to a process or a process group, and tell whether it still existed.

    Parameters
    ----------
    send
        Either `os.kill` or `os.killpg`.
    pid
        The process or process group to signal.
    sig
        The signal, of which 0 is not delivered: it only checks whether the target exists.
    """
    try:
        send(pid, sig)
    # `os.killpg` raises `PermissionError` on macOS for a group of zombies,
    # see `_signal_process_group` in `stepup.core.run`.
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate(pid: int):
    """Terminate a Windows process, which may have exited in the meantime."""
    with contextlib.suppress(OSError):
        # `os.kill` terminates a Windows process, whatever the signal.
        os.kill(pid, signal.SIGTERM)


async def assert_nothing_left(workdir: Path):
    """Check that nothing of an example is running anymore.

    On POSIX, the process group of `main.sh` must be empty and its step must have stopped.
    On macOS, a group with only zombies left counts as empty.
    Windows has no such group, so the director and its step are checked instead.
    Whatever is still running after `PROCESS_EXIT_WAIT` seconds is killed,
    so that a failing test does not leave it behind either.

    Parameters
    ----------
    workdir
        The directory in which the example ran.
    """
    step_pid = int((workdir / "step.pid").read_text(encoding="utf-8"))
    if sys.platform == "win32":
        _, director_pid, _ = query_director_log(workdir / DIRECTOR_LOG)
        assert director_pid is not None, "The director log does not hold the pid of the director."
        targets = [
            ("the director", is_process_running, _terminate, director_pid),
            ("the step", is_process_running, _terminate, step_pid),
        ]
    else:
        pgid = int((workdir / "main.pid").read_text(encoding="utf-8"))
        targets = [
            (
                "the process group of main.sh",
                functools.partial(_signal_reaches, os.killpg, sig=0),
                functools.partial(_signal_reaches, os.killpg, sig=signal.SIGKILL),
                pgid,
            ),
            (
                "the step",
                functools.partial(_signal_reaches, os.kill, sig=0),
                functools.partial(_signal_reaches, os.kill, sig=signal.SIGKILL),
                step_pid,
            ),
        ]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROCESS_EXIT_WAIT
    survivors = []
    for label, is_running, kill, pid in targets:
        while is_running(pid):
            if loop.time() > deadline:
                survivors.append(label)
                kill(pid)
                break
            await asyncio.sleep(0.1)
    assert not survivors, f"Still running: {', '.join(survivors)}"


async def test_run_example_stops_background_after_failure(path_tmp: Path):
    """The trap of `example.rc` stops what a failed `main.sh` left running in the background."""
    srcdir = write_harness_example(path_tmp, HARNESS_MAIN_FAILS)
    with pytest.raises(AssertionError):
        await run_example(srcdir, path_tmp / "run")
    workdir = path_tmp / "run" / "example"
    # `run_example` would stop `stepup build` anyway, so the trace of the trap is checked too.
    # The trap only finds `stepup build` when `main.sh` leads its own process group.
    sb_pid = (workdir / "sb.pid").read_text(encoding="utf-8").strip()
    main_log = (workdir / "main.log").read_text(encoding="utf-8")
    assert re.search(rf"^\++ kill .*\b{sb_pid}\b", main_log, re.MULTILINE), main_log
    await assert_nothing_left(workdir)


async def test_run_example_stops_everything_when_cancelled(path_tmp: Path):
    """Cancelling `run_example` stops `main.sh` and everything it started.

    A Ctrl-C of pytest cancels a test in the same way.
    """
    srcdir = write_harness_example(path_tmp, HARNESS_MAIN_HANGS)
    workdir = path_tmp / "run" / "example"
    task = asyncio.create_task(run_example(srcdir, path_tmp / "run"))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + EXAMPLE_TIMEOUT
    while not (workdir / "step.pid").is_file() and not task.done() and loop.time() < deadline:
        await asyncio.sleep(0.1)
    task.cancel()
    # A `run_example` that ended by itself raises its own error here, or nothing at all.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (workdir / "step.pid").is_file(), "The step never started."
    await assert_nothing_left(workdir)
