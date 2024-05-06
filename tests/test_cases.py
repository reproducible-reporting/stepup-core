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
"""Run examples."""

import os

import pytest
from path import Path

from stepup.core.pytest import run_example

OVERWRITE_EXPECTED = "STEPUP_OVERWRITE_EXPECTED" in os.environ


@pytest.mark.parametrize(
    "name",
    [
        "amend",
        "amend_delay",
        "amend_env_vars",
        "amend_missing",
        "amend_outdir_pending",
        "amend_voldir_pending",
        "deferred_glob1",
        "deferred_glob2",
        "deferred_subdir",
        "env_vars",
        "env_vars_amend",
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
        "here",
        "makedirs",
        "mkdir",
        "mkdir_error",
        "nodata",
        "noplan",
        "nostatic",
        "not_cyclic",
        "optional_convert",
        "output_not_created",
        "pending",
        "permissions_file_rerun",
        "permissions_plan_rerun",
        "permissions_plan_restart",
        "permissions_step_rerun",
        "permissions_step_restart",
        "pool",
        "restart_blocked",
        "restart_changes",
        "restart_deferred_glob",
        "restart_nochanges",
        "restart_outdated_amend",
        "restart_output",
        "restart_orphan",
        "reqdir_missing",
        "script_cases",
        "script_cases_settings",
        "script_single",
        "static",
        "static_abs",
        "static_dir",
        "static_glob",
        "static_nglob",
        "static_nglob_partial",
        "static_nglob_subdir",
        "subdir",
        "watch_blocked",
        "watch_boot",
        "watch_chain",
        "watch_input",
        "watch_middle",
        "watch_mixed",
        "watch_nochanges",
        "watch_outdated_amend1",
        "watch_outdated_amend2",
        "watch_output",
        "watch_wanted",
    ],
)
@pytest.mark.asyncio
async def test_example(tmpdir, name: str):
    await run_example(Path("tests/cases") / name, tmpdir, OVERWRITE_EXPECTED)
