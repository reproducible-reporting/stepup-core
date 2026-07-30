# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Reporting and cleanup at the end of a build phase.

`Builder.finalize` (in `builder.py`) calls, in this order:

- `revert_optional`, which puts optional steps back to `PENDING`,
- `report_completion`, which reports whatever could not be built and derives a `ReturnCode`,
- `remove_outdated_outputs`, which deletes files that are no longer built by any step.

None of these touch `Builder` state:
they work with the workflow, the scheduler and the reporter only,
which is why they live here instead of in `builder.py`.
"""

from collections.abc import Callable, Iterable, Iterator
from itertools import chain

from path import Path

from .enums import FileState, Need, ReturnCode, StepState
from .hash import FileHash
from .reporter import ReporterClient
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .step import PathRecord, Step
from .workflow import Workflow

__all__ = ("remove_outdated_outputs", "report_completion", "revert_optional")


#
# Revert optional steps
#


CREATE_OPTIONAL_STEP_TABLE = f"""
-- Find all optional steps
CREATE TEMP TABLE optional_step AS
SELECT step.node AS i, node.label, step.state
FROM step
JOIN node ON step.node = node.i
WHERE _implied_need = {Need.OPTIONAL.value}
AND NOT node.detached
"""

CREATE_OPTIONAL_FILE_TABLE = f"""
-- Find all files that are outputs or volatile of optional steps.
CREATE TEMP TABLE optional_to_be_deleted AS
SELECT node.i, node.label, file.state, file.hash
FROM file
JOIN node ON file.node = node.i
JOIN dependency ON dependency.sink = node.i
JOIN optional_step ON dependency.source = optional_step.i
WHERE file.state
IN ({FileState.VOLATILE.value}, {FileState.BUILT.value}, {FileState.OUTDATED.value})
"""

UPDATE_OPTIONAL_STEPS = f"""
UPDATE step SET state = {StepState.PENDING.value}
FROM optional_step
WHERE step.node = optional_step.i
AND step.state != {StepState.PENDING.value}
"""

SELECT_OPTIONAL_TO_BE_DELETED = """
SELECT label, state, hash FROM optional_to_be_deleted
"""

UPDATE_OPTIONAL_TO_BE_DELETED = f"""
UPDATE file
SET state = {FileState.AWAITED.value}, hash = NULL
FROM optional_to_be_deleted
WHERE file.node = optional_to_be_deleted.i
"""

DROP_OPTIONAL_STEP_TABLE = """
DROP TABLE IF EXISTS optional_step
"""

DROP_OPTIONAL_FILE_TABLE = """
DROP TABLE IF EXISTS optional_to_be_deleted
"""


async def revert_optional(db: DBSession, workflow: Workflow, reporter: ReporterClient):
    """Revert optional steps that have previously been executed to pending again."""
    async with db:
        # Drop before creating, too: a previous call that raised between the CREATE and the
        # DROP below would otherwise leave the temp tables behind on this connection,
        # making every later call fail on the CREATE.
        db.execute(DROP_OPTIONAL_STEP_TABLE)
        db.execute(DROP_OPTIONAL_FILE_TABLE)
        # Get the optional steps that are not pending, and mark them pending again.
        db.execute(CREATE_OPTIONAL_STEP_TABLE)
        db.execute(CREATE_OPTIONAL_FILE_TABLE)
        cur = db.execute(UPDATE_OPTIONAL_STEPS)
        nstep = cur.rowcount
        cur = db.execute(SELECT_OPTIONAL_TO_BE_DELETED)
        to_be_deleted = [
            (row[0], None if row[1] == FileState.VOLATILE.value else FileHash.from_json(row[2]))
            for row in cur
        ]
        if len(to_be_deleted) > 0:
            # Mark the files for deletion and reset their state in the database.
            workflow.to_be_deleted.extend(to_be_deleted)
            db.execute(UPDATE_OPTIONAL_TO_BE_DELETED)
        db.execute(DROP_OPTIONAL_STEP_TABLE)
        db.execute(DROP_OPTIONAL_FILE_TABLE)
    # Report the reverted steps and the files that are marked for deletion.
    if nstep > 0:
        await reporter("WARNING", f"Reverted {nstep} optional step(s) to PENDING.")
    if len(to_be_deleted) > 0:
        await reporter(
            "WARNING",
            f"Marked {len(to_be_deleted)} old optional (volatile) output file(s) for deletion.",
        )


#
# Report what could not be built
#


PENDING_REASON_TEXT = {
    "runnable": "runnable but not executed (builder was interrupted)",
    "inputs": "required inputs are unavailable",
    "resources": "required resources exceed maximum available",
    "unsafe": "creator is not RUNNING or SUCCEEDED",
}
"""Explanation of every reason returned by `Scheduler.get_pending_step_records`."""

STATE_COLUMN = 20
"""Column at which the state field of a line in a `PENDING Step` page ends.

Every block on the page aligns its state (or resource name) on this column,
whatever the width of the block's label,
so that the paths (or resource units) after it line up as well.
"""


def _column_lines(label: str, rows: Iterable[tuple[str, str]]) -> Iterator[str]:
    """Format `rows` as a block of aligned lines, labeled on the first line only.

    Parameters
    ----------
    label
        The label of the block, e.g. `"Inputs"`.
        It is printed on the first line and replaced by blanks on the following ones.
    rows
        The `(state, path)` pairs to format,
        where **state** is right-aligned on `STATE_COLUMN` and **path** follows it.

    Returns
    -------
    lines
        One line per row, empty when `rows` is empty.
    """
    for state, path in rows:
        yield f"{label}{state:>{STATE_COLUMN - len(label)}s}  {path}"
        label = ""


def _format_path(rec: PathRecord) -> str:
    """Format the path of `rec`, flagging it as detached and/or amended when relevant."""
    path = f"({rec.path})" if rec.detached else rec.path
    return f"{path} [amended]" if rec.amended else path


def _format_pending_step(step: Step, reason: str) -> str:
    """Format the page describing a step that remained pending and the reason why."""
    command, workdir = step.command_workdir
    header_width = STATE_COLUMN + 2
    lines = [
        f"{'Reason':<{header_width}}{PENDING_REASON_TEXT[reason]}",
        f"{'Command':<{header_width}}{command}",
    ]
    if workdir != ".":
        lines.append(f"{'Working directory':<{header_width}}{workdir}")
    lines.extend(
        _column_lines("Declares", ((rec.state.name, rec.path) for rec in step.static_paths()))
    )
    lines.extend(
        _column_lines("Declares", ((rec.state.name, rec.path) for rec in step.missing_paths()))
    )
    lines.extend(
        _column_lines(
            "Inputs",
            ((rec.state.name, _format_path(rec)) for rec in step.inp_paths(include_detached=True)),
        )
    )
    # Outputs and volatile outputs share one block, i.e. the label is not repeated.
    lines.extend(
        _column_lines(
            "Outputs",
            (
                (rec.state.name, _format_path(rec))
                for rec in chain(step.out_paths(), step.vol_paths())
            ),
        )
    )
    lines.extend(
        _column_lines("Resource", ((name, str(units)) for name, units in step.resources()))
    )
    return "\n".join(lines)


async def _report_pending_steps(
    db: DBSession, workflow: Workflow, scheduler: Scheduler, reporter: ReporterClient
) -> ReturnCode:
    """Report the steps that remained pending, with the reason why, if there are any."""
    async with db:
        step_records = scheduler.get_pending_step_records()
        if len(step_records) == 0:
            return ReturnCode(0)
        pages = [
            ("PENDING Step", _format_pending_step(step, reason)) for step, reason in step_records
        ]
        detached_page = "\n".join(
            f"{file_state.name:>{STATE_COLUMN}s}  {path}"
            for path, file_state in workflow.detached_inp_paths()
        )
        missing_page = "\n".join(
            f"{FileState.MISSING.name:>{STATE_COLUMN}s}  {path}"
            for path in workflow.missing_paths()
        )
    # Insert pages with detached and missing inputs in front.
    if detached_page != "":
        pages.insert(0, ("Detached inputs", detached_page))
    if missing_page != "":
        pages.insert(0, ("Missing inputs", missing_page))
    await reporter("WARNING", f"{len(step_records)} step(s) remained pending ...", pages)
    return ReturnCode.PENDING


async def _report_missing_targets(
    db: DBSession, workflow: Workflow, reporter: ReporterClient
) -> ReturnCode:
    """Report targets that no step in the workflow produces."""
    async with db:
        # Targets that never became the regular output of an active step: reported after the
        # build phase completes, since dynamically-declared steps may only appear once earlier
        # steps run, so this cannot be checked upfront.
        missing_targets = sorted(
            target for target in workflow.targets if not workflow.is_regular_output(target)
        )
        # Directory targets that matched zero regular outputs: weaker than the exact-target
        # warning above by design (best-effort semantics), see Workflow.dir_has_regular_output.
        missing_target_dirs = sorted(
            target_dir
            for target_dir in workflow.target_dirs
            if not workflow.dir_has_regular_output(target_dir)
        )
    returncode = ReturnCode(0)
    if len(missing_targets) > 0:
        returncode |= ReturnCode.NOTPRODUCED
        await reporter(
            "WARNING",
            f"{len(missing_targets)} target(s) are not produced by any step in the workflow: "
            + ", ".join(missing_targets),
        )
    if len(missing_target_dirs) > 0:
        returncode |= ReturnCode.NOTPRODUCED
        await reporter(
            "WARNING",
            f"{len(missing_target_dirs)} directory target(s) matched no regular output "
            "in the workflow: " + ", ".join(missing_target_dirs),
        )
    return returncode


async def report_completion(
    db: DBSession, workflow: Workflow, scheduler: Scheduler, reporter: ReporterClient
) -> ReturnCode:
    """Report parts of the workflow that could not be executed."""
    returncode = ReturnCode(0)
    async with db:
        nfailed = sum(1 for _ in workflow.steps(StepState.FAILED))
    if nfailed > 0:
        returncode |= ReturnCode.FAILED
        await reporter("WARNING", f"{nfailed} step(s) failed.")

    if scheduler.on_hold:
        returncode |= ReturnCode.ONHOLD
        await reporter("WARNING", "Scheduler is put on hold. Not reporting pending steps.")
        # The missing-target checks further down are skipped too: the build phase ended
        # early, so steps that would have declared a target as output may not have run yet,
        # making a "not produced" warning unreliable.
        return returncode

    returncode |= await _report_pending_steps(db, workflow, scheduler, reporter)
    returncode |= await _report_missing_targets(db, workflow, reporter)
    return returncode


#
# Remove outdated outputs
#


async def remove_outdated_outputs(db: DBSession, workflow: Workflow, reporter: ReporterClient):
    """Remove outdated outputs from the file system and reset their state in the database."""
    await reporter(
        "DIRECTOR",
        f"Trying to remove {len(workflow.to_be_deleted)} outdated output(s)",
    )
    workflow.to_be_deleted.sort(reverse=True)
    # Remove the files from the file system.
    parents = set()
    for path, file_hash in workflow.to_be_deleted:
        path = Path(path)
        if (file_hash is None or file_hash.regen(path) == file_hash) and _try_remove(path.remove):
            await reporter("REMOVE", path)
            parents.add(path.parent)

    await _prune_empty_dirs(parents, reporter)

    # Reset the state of the deleted files in the database, if they are still present.
    async with db:
        db.executemany(
            """
            WITH node_tmp AS (SELECT i FROM node WHERE label = ?)
            UPDATE file
            SET state = ?, hash = NULL
            WHERE node IN node_tmp
            """,
            [(path, FileState.AWAITED.value) for path, _ in workflow.to_be_deleted],
        )
    workflow.to_be_deleted.clear()


async def _prune_empty_dirs(parents: set[Path], reporter: ReporterClient):
    """Remove the directories in `parents`, and their own parents, as long as they are empty.

    Parameters
    ----------
    parents
        The directories to consider for removal,
        i.e. the parents of the files that were just removed.
    reporter
        Every removed directory is reported to this reporter.
    """
    # The sorted list is used as a stack, so the deepest directory is handled first.
    # The parent of a removed directory is pushed on top, i.e. out of sorted order,
    # so that the walk up towards the root continues right away.
    todo = sorted(parents)
    while len(todo) > 0:
        parent = todo.pop()
        if parent.is_dir() and not any(parent.iterdir()) and _try_remove(parent.rmdir):
            await reporter("REMOVE", parent)
            grandparent = parent.parent
            if grandparent.name not in ("..", ".", ""):
                todo.append(grandparent)


def _try_remove(remove: Callable[[], None]) -> bool:
    """Call `remove` (`Path.remove` or `Path.rmdir`) and return whether it succeeded.

    Any `OSError`, including `FileNotFoundError`, means "nothing was removed":
    the path may have been deleted or replaced by something else since the workflow
    recorded it, which is not worth interrupting the cleanup for.
    """
    try:
        remove()
    except OSError:
        return False
    return True
