# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Startup sequence after opening the database and configuring internal data structures."""

import glob
import logging
import os

from path import Path

from .builder import Builder
from .enums import FileState, HashUpdateCause, StepState
from .hash import FileHash, fmt_env_value, fmt_file_hash_diff
from .reporter import ReporterClient
from .sqlite3 import DBSession
from .step import Step
from .workflow import Workflow

__all__ = ("startup_from_db",)


logger = logging.getLogger(__name__)


async def startup_from_db(
    workflow: Workflow,
    db: DBSession,
    reporter: ReporterClient,
    builder: Builder,
):
    """Initialize internal datastructures by loading relevant parts from the database."""
    # Make steps pending if they are RUNNING, CHECKING, or FAILED.
    # RUNNING/CHECKING are uncommon, but can happen if the director crashes.
    async with db:
        # Steps that were running are considered failed.
        # This reset is also what recovers a stray UNCONFIRMED file left behind by a
        # director killed between declare_unconfirmed and confirm_hashes: rerunning the
        # step redeclares and confirms the file. Do not remove or narrow this without
        # another way to confirm such rows (see the guard in scan_file_changes below).
        db.execute(
            "UPDATE step SET state = ? WHERE state = ?",
            (StepState.FAILED.value, StepState.RUNNING.value),
        )
        # Steps that were being hash-checked go back to pending directly
        # (no output was produced, so no FAILED intermediate is needed).
        db.execute(
            "UPDATE step SET state = ? WHERE state = ?",
            (StepState.PENDING.value, StepState.CHECKING.value),
        )
        # Make all failed steps pending again, as they can be retried.
        _first_failed = True
        for step in workflow.steps(StepState.FAILED):
            if _first_failed:
                await reporter("STARTUP", "Making failed steps pending")
                _first_failed = False
            workflow.mark_step_pending(step)

    # Populate dir queue
    await populate_dir_queue(workflow, db, reporter)

    # Check for changes in environment variables used by steps.
    async with db:
        env_var_uses = db.execute(
            "SELECT node, label, name, value FROM env_var JOIN node ON env_var.node = node.i"
        ).fetchall()
    if len(env_var_uses) > 0:
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
        async with db:
            for step in to_mark_pending:
                workflow.mark_step_pending(step)

    # Check for file changes and new glob matches
    await reporter("STARTUP", "Scanning initial database for changed files")
    deleted, old_added = await scan_file_changes(workflow, db, reporter)
    await reporter("STARTUP", "Scanning initial database for new nglob matches")
    new_added = await scan_nglob_changes(workflow, db, reporter)
    async with db:
        workflow.process_nglob_changes(deleted, old_added | new_added)

    # Wrap up by making necessary steps pending and starting the builder.
    logger.info("Startup sequence completed")
    builder.resume.set()


async def populate_dir_queue(workflow: Workflow, db: DBSession, reporter: ReporterClient):
    sql = (
        "SELECT label FROM node JOIN file ON node.i = file.node WHERE kind = 'file' AND "
        f"file.state != {FileState.VOLATILE.value}"
    )
    async with db:
        rows = db.execute(sql).fetchall()
    if len(rows) > 0:
        parents = set()
        for (path,) in rows:
            parents.add(str(Path(path).parent))
        await reporter("STARTUP", f"Watching {len(parents)} director(y|ies) from initial database")
        for path in parents:
            workflow.put_dir_queue(path)


async def scan_file_changes(
    workflow: Workflow, db: DBSession, reporter: ReporterClient
) -> tuple[set[str], set[str]]:
    """Check all files in the workflow for changes."""
    sql = (
        "SELECT label, state, hash "
        "FROM node JOIN file ON node.i = file.node AND state NOT IN (?, ?) AND NOT detached"
    )
    data = (FileState.AWAITED.value, FileState.VOLATILE.value)
    async with db:
        rows = db.execute(sql, data).fetchall()
    if len(rows) == 0:
        return None

    deleted = set()
    added = set()
    changed_hashes = []
    for path, state, hash_value in rows:
        state = FileState(state)
        old_file_hash = FileHash.from_json(hash_value)
        new_file_hash = old_file_hash.regen(path)
        # An unchanged UNCONFIRMED row is deliberately left alone here: it is confirmed
        # instead by the RUNNING -> FAILED -> PENDING reset in startup_from_db,
        # which reruns the declaring step.
        if old_file_hash != new_file_hash:
            if new_file_hash.is_unknown:
                await reporter("DELETED", path)
                deleted.add(path)
            else:
                await reporter(
                    "UPDATED", path + " " + fmt_file_hash_diff(old_file_hash, new_file_hash)
                )
                if state in (FileState.MISSING, FileState.UNCONFIRMED, FileState.AWAITED):
                    added.add(path)
            changed_hashes.append((path, new_file_hash))

    logger.info("Updating file hashes %s", changed_hashes)
    async with db:
        workflow.update_file_hashes(changed_hashes, HashUpdateCause.EXTERNAL)
    return deleted, added


async def scan_nglob_changes(
    workflow: Workflow, db: DBSession, reporter: ReporterClient
) -> set[str]:
    """Look for new matches in nglobs used by some jobs."""
    # Load all nglob_multis
    async with db:
        nglob_multis = list(workflow.nglob_multis())

    # Collect potentially relevant paths
    paths = set()
    for ngm in nglob_multis:
        for ngs in ngm.nglob_singles:
            for path in glob.iglob(ngs.glob_pattern, recursive=True, include_hidden=True):
                if path not in paths and ngs.regex.fullmatch(path):
                    paths.add(path)

    # Select the new ones, i.e. not present in the workflow (detached or missing)
    async with db:
        db.execute("DELETE FROM path_list")
        db.executemany("INSERT INTO path_list VALUES (?)", ((path,) for path in paths))
        rows = db.execute(
            "SELECT path FROM path_list WHERE NOT EXISTS "
            "(SELECT 1 FROM node JOIN file ON node.i = file.node "
            # `node.kind = 'file'` is redundant (only file nodes have a `file` row), but
            # lets the correlated subquery's planner use the `node_kind_label` covering
            # index instead of a full scan of `node` for every candidate path.
            "WHERE node.kind = 'file' AND label = path AND "
            "NOT detached AND state NOT IN (?, ?))",
            (FileState.MISSING.value, FileState.UNCONFIRMED.value),
        ).fetchall()
    new_paths = [row[0] for row in rows]
    new_paths.sort()
    for new_path in new_paths:
        await reporter("UPDATED", new_path)
    return set(new_paths)
