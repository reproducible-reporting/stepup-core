# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Startup sequence after opening the database and configuring internal data structures."""

import logging
import os

from .builder import Builder
from .enums import FileState, HashUpdateCause, StepState
from .hash import fmt_env_value, fmt_file_hash_diff
from .hash_queue import gather_hashes
from .nglob import NamedGlob
from .path import parent_dir
from .reporter import ReporterClient
from .step import Step
from .workflow import Workflow

__all__ = ("resume_from_db",)


logger = logging.getLogger(__name__)


async def resume_from_db(workflow: Workflow, reporter: ReporterClient, builder: Builder):
    """Initialize internal datastructures by loading relevant parts from the database."""
    await reset_interrupted_steps(workflow, reporter)
    await watch_known_dirs(workflow, reporter)
    await rescan_env_vars(workflow, reporter)
    await rescan_files(workflow, reporter, builder)

    # Check for added / removed files that match nglobs used by some steps.
    # File content changes are not relevant for this check.
    await rescan_nglobs(workflow, reporter)

    logger.info("Startup sequence completed")


async def reset_interrupted_steps(workflow: Workflow, reporter: ReporterClient):
    """Make steps pending if they are RUNNING, CHECKING, or FAILED."""
    db = workflow.db
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
        # Interrupted hash jobs (related to `UNCONFIRMED` files) don't need to be handled here.
        # See `rescan_files()` for how they are resolved.
        failed_steps = workflow.steps(StepState.FAILED)

    # Make all failed steps pending again, as they can be retried.
    if len(failed_steps) > 0:
        await reporter("STARTUP", "Making failed steps pending")
        async with db:
            for step in failed_steps:
                workflow.mark_step_pending(step)


async def watch_known_dirs(workflow: Workflow, reporter: ReporterClient):
    """Hand the watcher every directory in which a relevant file may appear or disappear."""
    if workflow.dir_queue is None:
        return

    sql = (
        "SELECT label FROM node JOIN file ON node.i = file.node WHERE kind = 'file' AND "
        f"file.state != {FileState.VOLATILE.value}"
    )
    async with workflow.db:
        rows = workflow.db.execute(sql).fetchall()
        nglobs = [ng for _nglob_i, ng, _step in workflow.nglob_registrations()]

    # None of these directories is created here:
    # a directory is only created when a step is about to write into it.
    # A file node is watched through the same parent as when it was declared,
    # so a directory node is watched as itself. See `parent_dir` and `Workflow._declare_file`.
    dirs = {parent_dir(path) for (path,) in rows}
    for path in dirs:
        workflow.watch_dir(path)
    for ng in nglobs:
        dirs |= workflow.watch_nglob_dirs(ng)

    if len(dirs) > 0:
        noun = "directory" if len(dirs) == 1 else "directories"
        await reporter("STARTUP", f"Watching {len(dirs)} {noun}")


async def rescan_env_vars(workflow: Workflow, reporter: ReporterClient):
    """Check for changes in environment variables used by steps."""
    sql = (
        "SELECT node, label, name, value FROM env_var JOIN node ON env_var.node = node.i "
        "WHERE NOT node.detached"
    )
    async with workflow.db:
        env_var_uses = workflow.db.execute(sql).fetchall()

    # One step may use several changed variables, so it is collected only once.
    steps_to_rerun = {}
    reported_names = set()
    for node_i, label, name, old_value in env_var_uses:
        new_value = os.getenv(name)
        if new_value == old_value:
            continue
        steps_to_rerun[node_i] = Step(workflow, node_i, label)
        if name not in reported_names:
            reported_names.add(name)
            old_fmt = fmt_env_value(old_value)
            new_fmt = fmt_env_value(new_value)
            await reporter("UPDATED", f"{name} {old_fmt} ➜ {new_fmt}")

    if len(steps_to_rerun) > 0:
        async with workflow.db:
            for step in steps_to_rerun.values():
                workflow.mark_step_pending(step)


async def rescan_files(workflow: Workflow, reporter: ReporterClient, builder: Builder):
    """Check all relevant files in the workflow for changes.

    Which files are checked, and how detached ones are treated,
    is decided by `Workflow.get_all_observable_file_hashes`.

    This also settles a stray `UNCONFIRMED` row,
    left behind by a director killed while the hash job for that file was queued or in flight.
    The scan moves it to `CONFIRMED` or `MISSING`,
    so `reset_interrupted_steps()` does not have to rerun the step that declared it.
    """
    async with workflow.db:
        # The paths come out sorted, which is what puts the UPDATED and DELETED lines
        # reported below in a fixed order.
        old_hashes = workflow.get_all_observable_file_hashes()
    if len(old_hashes) == 0:
        return

    await reporter("STARTUP", f"Checking {len(old_hashes)} file(s) for changes")
    new_hashes = await gather_hashes(
        builder.hash_queue, builder.executor, reporter, list(old_hashes.items()), builder.njob
    )

    # The whole scan is applied in one transaction, rather than one per file.
    # No build phase is active here, so nothing contends for the lock while it is held.
    async with workflow.db:
        workflow.update_file_hashes(new_hashes, cause=HashUpdateCause.OBSERVED)

    # Keyed on the hash difference rather than on the update's outcome,
    # because what is reported here is the diff itself,
    # which does not exist when digest, size and mode all match:
    # `fmt_file_hash_diff` returns `None` in that case.
    for path, new_file_hash in new_hashes.items():
        old_file_hash = old_hashes[path]
        if old_file_hash != new_file_hash:
            if new_file_hash.is_unknown:
                await reporter("DELETED", path)
            else:
                await reporter(
                    "UPDATED", path + " " + fmt_file_hash_diff(old_file_hash, new_file_hash)
                )


async def rescan_nglobs(workflow: Workflow, reporter: ReporterClient) -> None:
    """Look for new and deleted matches in nglobs registered by steps, and process them.

    This is fully self-contained:
    for each nglob, the paths it matched last time (persisted in the `nglob` table)
    are compared to a fresh scan of the file system,
    without consulting the workflow's file states at all.
    Steps whose nglob matches changed are marked pending and their new matches are persisted.
    """
    async with workflow.db:
        registrations = list(workflow.nglob_registrations())
    if len(registrations) == 0:
        return

    # Compare the old matches (persisted from the previous run) to a fresh glob scan.
    await reporter("STARTUP", f"Checking {len(registrations)} nglob(s) for new or deleted matches")
    changed_nglobs = []
    all_deleted = set()
    all_added = set()
    for nglob_i, old_ng, step in registrations:
        old_paths = set(old_ng.files())
        # A fresh instance is built from scratch (rather than reusing or deep-copying `old_ng`)
        # because `NamedGlob.glob()` only ever adds matches: it has no mechanism to prune
        # paths that no longer exist, so it cannot detect deletions on its own.
        new_ng = NamedGlob(old_ng.pattern, old_ng.subs)
        new_ng.glob()
        new_paths = set(new_ng.files())
        deleted = old_paths - new_paths
        added = new_paths - old_paths
        if deleted or added:
            changed_nglobs.append((nglob_i, step, new_ng))
        all_deleted.update(deleted)
        all_added.update(added)

    for path in sorted(all_deleted):
        await reporter("DELETED", path)
    for path in sorted(all_added):
        await reporter("UPDATED", path)

    # A fresh scan is already the correct new state,
    # so there is no need to recompute it through Workflow.process_nglob_changes.
    if len(changed_nglobs) > 0:
        async with workflow.db:
            for nglob_i, step, new_ng in changed_nglobs:
                workflow.persist_nglob_matches(nglob_i, step, new_ng)
