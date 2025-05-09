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
"""Startup sequence after opening the database and configuring internal data structures."""

import glob
import logging
import os

from path import Path

from .enums import DirWatch, FileState, StepState
from .hash import FileHash, fmt_env_value, fmt_file_hash_diff
from .reporter import ReporterClient
from .runner import Runner
from .step import Step
from .utils import DBLock
from .workflow import Workflow

__all__ = ("startup_from_db",)


logger = logging.getLogger(__name__)


async def startup_from_db(
    workflow: Workflow, dblock: DBLock, runner: Runner, reporter: ReporterClient
):
    """Initialize internal datastructures by loading relevant parts from the database."""
    con = workflow.con

    await reporter("STARTUP", "Making failed steps pending")
    # Make steps pending if they are QUEUED, RUNNING or FAILED.
    # QUEUED, RUNNING are uncommon, but can happen if the director crashes.
    async with dblock:
        # Steps that were running are considered failed.
        con.execute(
            "UPDATE step SET state = ? WHERE state = ?",
            (StepState.FAILED.value, StepState.RUNNING.value),
        )
        # Steps that were queued are considered pending.
        con.execute(
            "UPDATE step SET state = ? WHERE state = ?",
            (StepState.PENDING.value, StepState.QUEUED.value),
        )
        # Call mark_pending on all failed steps.
        sql = "SELECT i, label FROM node JOIN step ON node.i = step.node WHERE state = ?"
        data = (StepState.FAILED.value,)
        for i, label in con.execute(sql, data):
            step = Step(workflow, i, label)
            step.mark_pending()

    # Define pools
    async with dblock:
        rows = con.execute("SELECT name, size FROM pool_definition GROUP BY name").fetchall()
    if len(rows) > 0:
        await reporter("STARTUP", f"Setting {len(rows)} pools from initial database")
        for pool, size in rows:
            workflow.config_queue.put_nowait((pool, size))
        workflow.job_queue_changed.set()

    # Populate dir queue
    if workflow.dir_queue is not None:
        sql = "SELECT label FROM node WHERE kind = 'file' AND label LIKE '%/'"
        async with dblock:
            rows = con.execute(sql).fetchall()
        if len(rows) > 0:
            await reporter("STARTUP", f"Watching {len(rows)} director(y|ies) from initial database")
            for (path,) in rows:
                workflow.dir_queue.put_nowait((DirWatch.START, Path(path)))

    # Check for changes in environment variables used by steps.
    async with dblock:
        env_var_uses = con.execute(
            "SELECT node, label, name, value FROM env_var JOIN node ON env_var.node = node.i"
        ).fetchall()
    if len(env_var_uses) > 0:
        await reporter("STARTUP", "Making steps pending that use changed environment variables")
        to_mark_pending = []
        seen = set()
        for i, label, name, value in env_var_uses:
            new_value = os.getenv(name)
            if new_value != value:
                to_mark_pending.append(Step(workflow, i, label))
                if name not in seen:
                    await reporter(
                        "UPDATED", f"{name} {fmt_env_value(value)} ➜ {fmt_env_value(new_value)}"
                    )
                    seen.add(name)
        async with dblock:
            for step in to_mark_pending:
                step.mark_pending(input_changed=True)

    # Check for file changes and new glob matches
    await reporter("STARTUP", "Scanning initial database for changed files")
    deleted, old_added = await scan_file_changes(workflow, dblock, reporter)
    await reporter("STARTUP", "Scanning initial database for new nglob matches")
    new_added = await scan_nglob_changes(workflow, dblock, reporter)
    async with dblock:
        workflow.process_nglob_changes(deleted, old_added | new_added)

    # Wrap up by making necessary steps pending and starting the runner.
    logger.info("Startup sequence completed")
    async with dblock:
        workflow.queue_pending_steps()
    runner.resume.set()


async def scan_file_changes(
    workflow: Workflow, dblock: DBLock, reporter: ReporterClient
) -> tuple[set[str], set[str]]:
    """Check all files in the workflow for changes."""
    sql = (
        "SELECT label, state, digest, mode, mtime, size, inode "
        "FROM node JOIN file ON node.i = file.node AND state NOT IN (?, ?) AND NOT orphan"
    )
    data = (FileState.AWAITED.value, FileState.VOLATILE.value)
    con = workflow.con
    async with dblock:
        rows = con.execute(sql, data).fetchall()
    if len(rows) == 0:
        return None

    deleted = set()
    added = set()
    changed_hashes = []
    for path, state, digest, mode, mtime, size, inode in rows:
        state = FileState(state)
        old_file_hash = FileHash(digest, mode, mtime, size, inode)
        new_file_hash = old_file_hash.regen(path)
        if old_file_hash != new_file_hash:
            if new_file_hash.is_unknown:
                await reporter("DELETED", path)
                deleted.add(path)
            else:
                await reporter(
                    "UPDATED", path + " " + fmt_file_hash_diff(old_file_hash, new_file_hash)
                )
                if state in (FileState.MISSING, FileState.AWAITED):
                    added.add(path)
            changed_hashes.append((path, new_file_hash))

    logger.info("Updating file hashes %s", changed_hashes)
    async with dblock:
        workflow.update_file_hashes(changed_hashes, "external")
    return deleted, added


async def scan_nglob_changes(
    workflow: Workflow, dblock: DBLock, reporter: ReporterClient
) -> set[str]:
    """Look for new matches in nglobs used by some jobs."""
    # Load all nglob_multis
    async with dblock:
        nglob_multis = list(workflow.nglob_multis())

    # Collect potentially relevant paths
    paths = set()
    for ngm in nglob_multis:
        for ngs in ngm.nglob_singles:
            for path in glob.iglob(ngs.glob_pattern, recursive=True, include_hidden=True):
                if Path(path).is_dir() and not path.endswith("/"):
                    path += "/"
                if ngs.regex.fullmatch(path) and path not in paths:
                    paths.add(path)

    # Select the new ones, i.e. not present in the workflow (orphan or missing)
    con = workflow.con
    async with dblock:
        con.execute("DROP TABLE IF EXISTS temp.glob")
        con.execute("CREATE TABLE temp.glob (path TEXT)")
        con.executemany("INSERT INTO temp.glob VALUES (?)", ((path,) for path in paths))
        rows = con.execute(
            "SELECT path FROM temp.glob WHERE NOT EXISTS "
            "(SELECT 1 FROM node JOIN file ON node.i = file.node "
            "WHERE label = path AND "
            "NOT orphan AND state != ?)",
            (FileState.MISSING.value,),
        ).fetchall()
        con.execute("DROP TABLE IF EXISTS temp.glob")
    new_paths = [row[0] for row in rows]
    new_paths.sort()
    for new_path in new_paths:
        await reporter("UPDATED", new_path)
    return set(new_paths)
