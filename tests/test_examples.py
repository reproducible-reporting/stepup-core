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
"""Run examples."""

import os

import pytest
from path import Path

from stepup.core.pytest import run_example, run_plan

OVERWRITE_EXPECTED = "STEPUP_OVERWRITE_EXPECTED" in os.environ


@pytest.mark.parametrize(
    "name",
    [
        "absolute",
        "amend",
        "amend_delay",
        "amend_delete_both",
        "amend_env_vars",
        "amend_missing",
        "amend_outdir_pending",
        "amend_self_static",
        "amend_validate1",
        "amend_validate2",
        "amend_voldir_pending",
        "call_chain",
        "call_import",
        "call_inp_out",
        "call_json_pickle",
        "call_kwargs_return",
        "call_minimal",
        "call_not_json",
        "call_out",
        "call_pickle",
        "call_prefix",
        "call_subdir",
        "clean_after",
        "clean_awaited1",
        "clean_awaited2",
        "clean_changed",
        "clean_dir",
        "clean_dir_static",
        "clean_drained_scheduler",
        "clean_nonexisting",
        "cyclic_dynamic",
        "cyclic_static",
        "deferred_dir_amend",
        "deferred_dir_glob",
        "deferred_dir_static",
        "deferred_dir_step",
        "deferred_glob1",
        "deferred_glob2",
        "deferred_nonexisting_dir",
        "deferred_subdir",
        "env_vars",
        "env_vars_amend",
        "env_vars_multi",
        "env_vars_path",
        "env_vars_subs",
        "env_vars_workdir",
        "error_cyclic_late",
        "error_deferred_nonexisting",
        "error_env_var",
        "error_main_fails",
        "error_not_executable",
        "error_overlap_deferred",
        "error_static_dir",
        "error_step",
        "external_sources",
        "getinfo",
        "here",
        "inp_digest",
        "input_check_changed",
        "input_check_deleted",
        "input_check_three",
        "input_check_two",
        "makedirs",
        "mkdir",
        "mkdir_error",
        "no_clean",
        "no_data",
        "no_plan",
        "no_shebang",
        "no_static",
        "not_cyclic",
        "optional_amend",
        "optional_amend_twice",
        "optional_chain",
        "optional_change",
        "optional_change_sub",
        "optional_cleanup",
        "optional_convert",
        "output_not_created",
        "pending",
        "pending_cleanup",
        "pending_nested",
        "permissions_file_rerun",
        "permissions_plan_rerun",
        "permissions_plan_restart",
        "permissions_step_rerun",
        "permissions_step_restart",
        "plan_inp",
        "pool",
        "pool_restart",
        "recreate_elsewhere",
        "render_jinja_dict",
        "render_jinja_json",
        "render_jinja_python",
        "render_jinja_relpath",
        "render_jinja_toml",
        "render_jinja_yaml",
        "replay_deferred",
        "replay_nglob",
        "replay_static",
        "restart_add_missing",
        "restart_add_nglob",
        "restart_amend_concerted",
        "restart_blocked",
        "restart_changes",
        "restart_deferred_glob",
        "restart_deferred_removed",
        "restart_delete_nglob",
        "restart_failed",
        "restart_fix_bug",
        "restart_input",
        "restart_mandatory",
        "restart_mode_change",
        "restart_nested",
        "restart_nochanges",
        "restart_orphan",
        "restart_outdated_amend",
        "restart_output",
        "restart_queued",
        "restart_static_remove1",
        "restart_static_remove2",
        "restart_step_changed",
        "reqdir_missing",
        "runnable",
        "script_cases",
        "script_cases_run_import",
        "script_cases_settings",
        "script_inp",
        "script_single",
        "script_single_run_import",
        "static",
        "static_abs",
        "static_dir",
        "static_glob",
        "static_nglob",
        "static_nglob_partial",
        "static_nglob_recursive",
        "static_nglob_subdir",
        "subdir",
        "translate_external",
        "wait_first",
        "watch_add_missing",
        "watch_amended",
        "watch_awaited",
        "watch_blocked",
        "watch_boot",
        "watch_chain",
        "watch_failed",
        "watch_input",
        "watch_mandatory",
        "watch_middle",
        "watch_mixed",
        "watch_mode_change",
        "watch_move_dir_simple",
        "watch_nochanges",
        "watch_outdated_amend1",
        "watch_outdated_amend2",
        "watch_output",
        "watch_static_remove1",
        "watch_static_remove2",
        "watch_volatile",
        "watch_wanted",
        "whitespace",
        "workdir",
    ],
)
@pytest.mark.asyncio
async def test_example(name: str, path_tmp: Path):
    await run_example(Path("tests/examples") / name, path_tmp, OVERWRITE_EXPECTED)


@pytest.mark.parametrize(
    "name",
    [
        "amend",
        "deferred_nonexisting_dir",
        "not_cyclic",
        "optional_convert",
        "static",
    ],
)
@pytest.mark.asyncio
async def test_plan(name: str, path_tmp: Path):
    await run_plan(Path("tests/examples") / name, path_tmp)
