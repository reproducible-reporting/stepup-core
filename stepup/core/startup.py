# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Startup sequence after opening the database and configuring internal data structures."""

import json
import logging
import os

from path import Path

from .builder import Builder
from .cattrs import json_converter
from .enums import FileState, HashUpdateCause, StepState
from .hash import FileHash, fmt_env_value, fmt_file_hash_diff
from .hash_queue import gather_hashes
from .nglob import NamedGlob, glob_base_dir
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
    await reset_to_pending(workflow, db, reporter)
    await populate_dir_queue(workflow, db, reporter)
    await check_env_changes(workflow, db, reporter)
    await check_file_changes(db, reporter, builder)

    # Check for added / removed files that match nglobs used by some steps.
    # File content changes are not relevant for this check.
    await check_nglob_changes(workflow, db, reporter)

    # Every step that must run again is pending now, so the builder can start.
    logger.info("Startup sequence completed")
    builder.resume.set()


async def reset_to_pending(workflow: Workflow, db: DBSession, reporter: ReporterClient):
    """Make steps pending if they are RUNNING, CHECKING, or FAILED."""

    # RUNNING/CHECKING are uncommon, but can happen if the director crashes.
    async with db:
        # Steps that were running are considered failed.
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
        # Interrupted hash jobs (related to `UNCONFIRMED` files) don't need to be handled here.
        # See `check_file_changes()` for how they are resolved.


async def populate_dir_queue(workflow: Workflow, db: DBSession, reporter: ReporterClient):
    """Populate the workflow's directory queue with directories to watch."""
    sql = (
        "SELECT label FROM node JOIN file ON node.i = file.node WHERE kind = 'file' AND "
        f"file.state != {FileState.VOLATILE.value}"
    )
    async with db:
        rows = db.execute(sql).fetchall()
        nglobs = [ng for _nglob_i, ng, _step in workflow.nglob_registrations()]

    # None of these directories is created here:
    # a directory is only created when a step is about to write into it.
    # A root-level path's parent is "", normalized to "." so it folds into the same set entry
    # as glob_base_dir's own root value, keeping the reported count accurate.
    parents = {str(Path(path).parent) or "." for (path,) in rows}
    for ng in nglobs:
        for path in ng.files():
            parents.add(str(Path(path.rstrip(os.sep)).parent) or ".")
        parents.add(glob_base_dir(ng.pattern))

    if len(parents) > 0:
        await reporter("STARTUP", f"Watching {len(parents)} director(y|ies)")
        for path in parents:
            workflow.watch_dir(path)


async def check_env_changes(workflow: Workflow, db: DBSession, reporter: ReporterClient):
    """Check for changes in environment variables used by steps."""
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


async def check_file_changes(db: DBSession, reporter: ReporterClient, builder: Builder):
    """Check all relevant files in the workflow for changes.

    The following are not checked:
    - Files in the VOLATILE state: they are expected to change.
    - Files in the PLANNED state: they are not yet built, so their content is not relevant.
    - Detached files: they are not part of the workflow, so their content is not relevant.
    """
    sql = (
        "SELECT label, state, hash "
        "FROM node JOIN file ON node.i = file.node AND state NOT IN (?, ?) AND NOT detached"
    )
    data = (FileState.PLANNED.value, FileState.VOLATILE.value)
    async with db:
        rows = db.execute(sql, data).fetchall()
    if len(rows) == 0:
        return

    await reporter("STARTUP", f"Checking {len(rows)} file(s) for changes")
    # Stray `UNCONFIRMED` rows, left behind by a director killed
    # while their confirming hash job was still queued or in flight,
    # are resolved directly here, via the `CONFIRMED` cause,
    # rather than depending on the `RUNNING` -> `FAILED` -> `PENDING` reset
    # in `startup_from_db()` to rerun the declaring step.
    # `CONFIRMED` is the only cause that flips `UNCONFIRMED` -> `CONFIRMED`/`MISSING`,

    old_by_path = {}
    path_hash_causes = []
    for path, state, hash_value in rows:
        old_file_hash = FileHash.from_json(hash_value)
        old_by_path[path] = old_file_hash
        cause = (
            HashUpdateCause.CONFIRMED
            if FileState(state) == FileState.UNCONFIRMED
            else HashUpdateCause.EXTERNAL
        )
        path_hash_causes.append((path, old_file_hash, cause))
    new_hashes = await gather_hashes(
        builder.hash_queue, builder.executor, reporter, path_hash_causes, builder.njob
    )

    for path, new_file_hash in new_hashes.items():
        old_file_hash = old_by_path[path]
        if old_file_hash != new_file_hash:
            if new_file_hash.is_unknown:
                await reporter("DELETED", path)
            else:
                await reporter(
                    "UPDATED", path + " " + fmt_file_hash_diff(old_file_hash, new_file_hash)
                )


async def check_nglob_changes(workflow: Workflow, db: DBSession, reporter: ReporterClient) -> None:
    """Look for new and deleted matches in nglobs registered by steps, and process them.

    This is fully self-contained:
    for each nglob, the paths it matched last time (persisted in the `nglob` table)
    are compared to a fresh scan of the file system,
    without consulting the workflow's file states at all.
    Steps whose nglob matches changed are marked pending and their new matches are persisted.
    """
    async with db:
        nglobs = list(workflow.nglob_registrations())
    if len(nglobs) == 0:
        return

    # Compare the old matches (persisted from the previous run) to a fresh glob scan.
    await reporter("STARTUP", f"Checking {len(nglobs)} nglob(s) for new or deleted matches")
    changed = []
    all_deleted = set()
    all_added = set()
    for i, ng, step in nglobs:
        old_paths = set(ng.files())
        # A fresh instance is built from scratch (rather than reusing or deep-copying `ng`)
        # because `NamedGlob.glob()` only ever adds matches: it has no mechanism to prune
        # paths that no longer exist, so it cannot detect deletions on its own.
        fresh = NamedGlob(ng.pattern, ng.subs)
        fresh.glob()
        new_paths = set(fresh.files())
        local_deleted = old_paths - new_paths
        local_added = new_paths - old_paths
        if local_deleted or local_added:
            changed.append((i, step, fresh))
        all_deleted.update(local_deleted)
        all_added.update(local_added)

    for path in sorted(all_deleted):
        await reporter("DELETED", path)
    for path in sorted(all_added):
        await reporter("UPDATED", path)

    # Persist the freshly scanned matches of the nglobs whose matches actually changed,
    # and mark their owning steps pending.
    # A fresh scan is already the correct new state,
    # so there is no need to recompute it through Workflow.process_nglob_changes.
    if len(changed) > 0:
        async with db:
            for i, step, fresh in changed:
                step.delete_hash()
                data = (json.dumps(json_converter.unstructure(fresh)), i)
                db.execute("UPDATE nglob SET data = ? WHERE i = ?", data)
                workflow.mark_step_pending(step)
