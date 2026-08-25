# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Reporting and cleanup at the end of a build phase.

This module provides three independent entry points for `Builder.finalize`,
listed in the order in which they are called:

- `report_unbuilt`, which reports whatever could not be built and derives a `ReturnCode`,
- `revert_optional_steps`, which puts optional steps back to `PENDING`,
- `remove_deletable_files`, which deletes files that are no longer built by any step.

The last two form the cleanup pass and are skipped together,
e.g. after an incomplete build.

None of these touch `Builder` state:
they work with the database, the workflow, the scheduler and the reporter only,
which is why they live here instead of in `builder.py`.
"""

import os
from collections.abc import Callable, Iterable
from itertools import groupby

from path import Path

from .enums import FileState, Need, ReturnCode, StepState
from .exceptions import HashError
from .hash import FileHash
from .pending import PendingSummary, analyze_pending
from .reporter import ReporterClient
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .workflow import GlobViolation, Workflow, mark_dir_to_be_deleted

__all__ = ("remove_deletable_files", "report_unbuilt", "revert_optional_steps")


#
# Report what could not be built
#


STATE_COLUMN = 20
"""Column at which the state (or resource name) field of a report line ends."""

PENDING_INPUT_REMEDY = """\
Create the input file(s) listed above, correct their static() paths,
or add a step that builds them."""

PENDING_DETACHED_REMEDY = """\
Paths in parentheses are detached: no step in the workflow declares them."""

PENDING_RESOURCE_REMEDY = """\
Increase resources with --resources or lower the step requirements."""

PENDING_BROWSE_REMEDY = """\
Run `stepup browse` and search for a name above to see the steps involved."""


def _format_table_lines(
    rows: list[tuple[str, str, str]], detail_width: int, count_width: int
) -> list[str]:
    """Format `rows` as a block of aligned `Unavailable inputs` / `Insufficient resources` lines.

    Parameters
    ----------
    rows
        `(key, detail, count)` triples, where **key** is a `FileState` name or a resource name
        (or `""` for a remainder row), right-aligned on `STATE_COLUMN`,
        **detail** is left-aligned on `detail_width`,
        and **count** is right-aligned on `count_width` and followed by the literal `" step(s)"`.
        An empty **count** drops the count cell, and the padding before it, from the line.
    detail_width
        The width to left-align **detail** on.
    count_width
        The width to right-align **count** on.

    Returns
    -------
    lines
        One line per row.
    """
    lines = []
    for key, detail, count in rows:
        line = f"{key:>{STATE_COLUMN}s}: {detail:<{detail_width}s}"
        if len(count) > 0:
            line += f"  {count:>{count_width}s} step(s)"
        lines.append(line.rstrip())
    return lines


def _format_hidden_count(nhidden_blocked: int) -> str:
    """Format the count cell of a remainder row, given its attributed step count.

    The number is only a lower bound
    (see `PendingSummary.ninputs_hidden_blocked` and `PendingSummary.nresources_hidden_blocked`),
    and a lower bound of zero says nothing at all,
    so it is left out instead of printed as `≥ 0`.
    """
    return "" if nhidden_blocked == 0 else f"≥ {nhidden_blocked}"


def _format_input_rows(summary: PendingSummary) -> list[tuple[str, str, str]]:
    """Build the `(key, detail, count)` rows of the `Unavailable inputs` table.

    The key is the file state and the detail is the path.
    """
    rows = [
        (row.state.name, f"({row.path})" if row.detached else row.path, str(row.nblocked))
        for row in summary.inputs
    ]
    if summary.ninputs_hidden > 0:
        rows.append(
            (
                "",
                f"... and {summary.ninputs_hidden} more input(s)",
                _format_hidden_count(summary.ninputs_hidden_blocked),
            )
        )
    return rows


def _format_units_available(units_available: int | None) -> str:
    """Format the `available` clause of an `Insufficient resources` row."""
    return "none available" if units_available is None else f"{units_available} available"


def _format_resource_rows(summary: PendingSummary) -> list[tuple[str, str, str]]:
    """Build the `(key, detail, count)` rows of the `Insufficient resources` table.

    The key is the resource name and the detail describes needed versus available units.
    """
    rows = [
        (
            row.name,
            f"{row.units_needed} unit(s) needed, {_format_units_available(row.units_available)}",
            str(row.nblocked),
        )
        for row in summary.resources
    ]
    if summary.nresources_hidden > 0:
        rows.append(
            (
                "",
                f"... and {summary.nresources_hidden} more resource(s)",
                _format_hidden_count(summary.nresources_hidden_blocked),
            )
        )
    return rows


def _format_other_lines(summary: PendingSummary) -> list[str]:
    """Format the `Other reasons` page: one prose line per non-empty bucket."""
    lines = []
    for bucket, template in (
        (summary.failed, "{n} step(s) are blocked by failed steps, e.g. {example}."),
        (summary.cyclic, "{n} step(s) are waiting on each other, e.g. {example}."),
        (
            summary.deferred,
            "{n} step(s) are deferred, yet none of their inputs is unavailable, e.g. {example}.",
        ),
        (
            summary.other,
            "{n} step(s) are blocked by a step that is not reported here, e.g. {example}.",
        ),
        (
            summary.runnable,
            "{n} step(s) seem runnable, e.g. {example}; the build phase may have ended early.",
        ),
    ):
        if bucket.nblocked == 0:
            continue
        # The `, e.g. {example}` clause is always included:
        # `PendingOther.example` is `None` only when `nblocked == 0`.
        assert bucket.example is not None
        lines.append(template.format(n=bucket.nblocked, example=bucket.example))
    return lines


def _format_remedy_lines(summary: PendingSummary) -> list[str]:
    """Format the `Remedy` page: one paragraph per pending-step problem actually shown."""
    lines = []
    if len(summary.inputs) > 0:
        lines.append(PENDING_INPUT_REMEDY)
        if any(row.detached for row in summary.inputs):
            lines.append(PENDING_DETACHED_REMEDY)
    if len(summary.resources) > 0:
        lines.append(PENDING_RESOURCE_REMEDY)
    lines.append(PENDING_BROWSE_REMEDY)
    return lines


async def _report_pending_steps(workflow: Workflow, reporter: ReporterClient) -> ReturnCode:
    """Report a fixed-size root-cause summary of the steps that remained pending, if any."""
    async with workflow.db:
        summary = analyze_pending(workflow)
    if summary.ntotal == 0:
        return ReturnCode(0)

    input_rows = _format_input_rows(summary)
    resource_rows = _format_resource_rows(summary)
    table_rows = input_rows + resource_rows
    # Both tables share their column widths, so their pages line up with each other.
    detail_width = max((len(detail) for _, detail, _ in table_rows), default=0)
    count_width = max((len(count) for _, _, count in table_rows), default=0)

    pages = []
    if len(input_rows) > 0:
        lines = _format_table_lines(input_rows, detail_width, count_width)
        pages.append(("Unavailable inputs", "\n".join(lines)))
    if len(resource_rows) > 0:
        lines = _format_table_lines(resource_rows, detail_width, count_width)
        pages.append(("Insufficient resources", "\n".join(lines)))
    other_lines = _format_other_lines(summary)
    if len(other_lines) > 0:
        pages.append(("Other reasons", "\n".join(other_lines)))
    if len(pages) > 0:
        # Unconditional once any other page is present: the `stepup browse` line always applies,
        # and `_format_remedy_lines` gates `PENDING_INPUT_REMEDY` and `PENDING_RESOURCE_REMEDY`
        # on the very tables that make this page non-empty in the first place.
        pages.append(("Remedy", "\n".join(_format_remedy_lines(summary))))

    await reporter("WARNING", f"{summary.ntotal} step(s) remained pending.", pages)
    return ReturnCode.PENDING


async def _report_missing_targets(workflow: Workflow, reporter: ReporterClient) -> ReturnCode:
    """Report targets that no step in the workflow produces."""
    async with workflow.db:
        # Targets that never became the regular output of an active step.
        # This cannot be checked upfront,
        # because dynamically declared steps may only appear once earlier steps have run.
        missing_targets = sorted(
            target for target in workflow.targets if not workflow.is_regular_output(target)
        )
        # Directory targets that matched zero regular outputs.
        # This check is weaker than the exact-target one above by design (best-effort semantics).
        # See `Workflow.has_regular_output_under`.
        missing_target_dirs = sorted(
            target_dir
            for target_dir in workflow.target_dirs
            if not workflow.has_regular_output_under(target_dir)
        )
    returncode = ReturnCode(0)
    if len(missing_targets) > 0:
        await reporter(
            "WARNING",
            f"{len(missing_targets)} target(s) are not produced by any step in the workflow: "
            + ", ".join(missing_targets),
        )
        returncode |= ReturnCode.WARNING
    if len(missing_target_dirs) > 0:
        await reporter(
            "WARNING",
            f"{len(missing_target_dirs)} directory target(s) matched no regular output "
            "in the workflow: " + ", ".join(missing_target_dirs),
        )
        returncode |= ReturnCode.WARNING
    return returncode


GLOB_VIOLATION_REMEDY = """\
Every matched file must be declared static or lie inside a static tree.
A matched directory also qualifies when it contains a static file or tree.
Declare the files with static(), or declare their directory as a static tree."""


def _format_glob_violation_state(state: FileState | None) -> str:
    """Format the state column of a `GlobViolation` row: the state name, or `(no node)`."""
    return "(no node)" if state is None else state.name


def _format_glob_violation_pages(violations: Iterable[GlobViolation]) -> list[tuple[str, str]]:
    """Format one page per (step, pattern) group of `violations`, aligned on `STATE_COLUMN`.

    The violations must be sorted, as `Workflow.find_glob_violations` returns them.
    """
    return [
        (
            f"{step_label}: {pattern}",
            "\n".join(
                f"{_format_glob_violation_state(violation.state):>{STATE_COLUMN}s}  "
                f"{violation.path}"
                for violation in group
            ),
        )
        for (step_label, pattern), group in groupby(
            violations, key=lambda violation: (violation.step_label, violation.pattern)
        )
    ]


async def _report_glob_violations(workflow: Workflow, reporter: ReporterClient) -> ReturnCode:
    """Report glob matches that no static declaration justifies."""
    async with workflow.db:
        violations = workflow.find_glob_violations()
    returncode = ReturnCode(0)
    warnings = [violation for violation in violations if not violation.is_error]
    errors = [violation for violation in violations if violation.is_error]
    if len(warnings) > 0:
        returncode |= ReturnCode.WARNING
        pages = _format_glob_violation_pages(warnings)
        pages.append(("Remedy", GLOB_VIOLATION_REMEDY))
        await reporter("WARNING", f"{len(warnings)} glob match(es) are not declared static.", pages)
    if len(errors) > 0:
        returncode |= ReturnCode.FAILED
        await reporter(
            "ERROR",
            f"{len(errors)} glob match(es) are files that a step builds.",
            _format_glob_violation_pages(errors),
        )
    return returncode


async def report_unbuilt(
    workflow: Workflow, scheduler: Scheduler, reporter: ReporterClient
) -> ReturnCode:
    """Report parts of the workflow that could not be executed."""
    returncode = ReturnCode(0)
    async with workflow.db:
        nfailed = sum(1 for _ in workflow.steps(StepState.FAILED))
    if nfailed > 0:
        returncode |= ReturnCode.FAILED
        await reporter("WARNING", f"{nfailed} step(s) failed.")

    if scheduler.draining:
        returncode |= ReturnCode.DRAINED
        await reporter("WARNING", "Scheduler is draining. Not reporting pending steps.")
        # The missing-target checks further down are skipped too: the build phase ended
        # early, so steps that would have declared a target as output may not have run yet,
        # making a "not produced" warning unreliable.
        return returncode

    returncode |= await _report_pending_steps(workflow, reporter)
    returncode |= await _report_missing_targets(workflow, reporter)
    # Late glob validation is skipped when the build already went wrong: an unjustified
    # match is then usually a consequence (a plan that would have declared it never ran),
    # and fixing the real failure tends to fix this too.
    if returncode == ReturnCode(0):
        returncode |= await _report_glob_violations(workflow, reporter)
    return returncode


#
# Revert optional steps
#


OPTIONAL_TABLE_NAMES = ("optional_step", "optional_to_be_deleted")

CREATE_OPTIONAL_STEP_TABLE = f"""
-- Find all attached optional steps.
CREATE TEMP TABLE optional_step AS
SELECT step.node AS i, node.label, step.state
FROM step
JOIN node ON step.node = node.i
WHERE _implied_need = {Need.OPTIONAL.value}
AND NOT node.detached
"""

CREATE_OPTIONAL_TO_BE_DELETED_TABLE = f"""
-- Find all files that are regular or volatile outputs of optional steps.
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
-- Put the regular outputs of the reverted steps back to PLANNED,
-- but leave the volatile ones in VOLATILE.
-- VOLATILE is the only state in its role,
-- so resetting such a row would migrate it into the OUTPUT role,
-- where out_paths() would count it as a regular output
-- and nothing would move it back until the step is redeclared.
-- Volatile rows stay in optional_to_be_deleted either way:
-- they must still be removed from disk.
UPDATE file
SET state = {FileState.PLANNED.value}, hash = NULL
FROM optional_to_be_deleted
WHERE file.node = optional_to_be_deleted.i
AND file.state != {FileState.VOLATILE.value}
"""


def _drop_optional_tables(db: DBSession):
    """Drop every `optional_*` scratch table, if present."""
    for name in OPTIONAL_TABLE_NAMES:
        db.execute(f"DROP TABLE IF EXISTS {name}")


async def revert_optional_steps(
    workflow: Workflow, reporter: ReporterClient
) -> dict[str, FileHash | None]:
    """Revert optional steps that have been executed earlier back to PENDING.

    Their outputs are reset in the database and their paths are returned,
    so `remove_deletable_files` can take them off disk.

    Returns
    -------
    to_be_deleted
        The paths of the reset outputs, and their parent directories.
    """
    db = workflow.db
    async with db:
        # Drop before creating out of precaution.
        # (In principle redundant, because async with db rolls back on error.)
        _drop_optional_tables(db)
        # Get the optional steps that are not pending, and mark them pending again.
        db.execute(CREATE_OPTIONAL_STEP_TABLE)
        db.execute(CREATE_OPTIONAL_TO_BE_DELETED_TABLE)
        cur = db.execute(UPDATE_OPTIONAL_STEPS)
        nstep = cur.rowcount
        cur = db.execute(SELECT_OPTIONAL_TO_BE_DELETED)
        to_be_deleted = {
            row[0]: None if row[1] == FileState.VOLATILE.value else FileHash.from_json(row[2])
            for row in cur
        }
        if len(to_be_deleted) > 0:
            # Mark the files for deletion and reset their state in the database.
            # Their nodes stay in the graph, so `File.before_delete` does not run for them
            # and their directories have to be marked here.
            for path in list(to_be_deleted):
                mark_dir_to_be_deleted(to_be_deleted, Path(path).parent)
            db.execute(UPDATE_OPTIONAL_TO_BE_DELETED)
        # Drop in the end: the temp tables are only needed for the duration of this call.
        _drop_optional_tables(db)
    # Report the reverted steps and the files that are marked for deletion.
    if nstep > 0:
        await reporter("WARNING", f"Reverted {nstep} optional step(s) to PENDING.")
    nfile = sum(1 for path in to_be_deleted if not path.endswith(os.sep))
    if nfile > 0:
        await reporter(
            "WARNING",
            f"Marked {nfile} output file(s) of reverted step(s) for deletion.",
        )
    return to_be_deleted


#
# Remove deletable files
#


async def remove_deletable_files(
    to_be_deleted: dict[str, FileHash | None], reporter: ReporterClient
):
    """Remove the files in `to_be_deleted`, and the directories left empty.

    The database needs no update here, because every queued path is settled already:
    a path queued by `File.before_delete` has lost its node in `Trellis.delete_detached`,
    taking the row in the file table with it,
    and a path queued by `revert_optional_steps` had its row reset there.

    Parameters
    ----------
    to_be_deleted
        The deletion queue, see `Workflow.to_be_deleted` for its layout.
    reporter
        Every removed path is reported to this reporter.
    """
    file_paths = [path for path in to_be_deleted if not path.endswith(os.sep)]
    await reporter(
        "DIRECTOR",
        f"Trying to remove {len(file_paths)} deletable file(s) and empty director(y|ies)",
    )
    # Remove the files from the file system.
    for file_path in sorted(file_paths, reverse=True):
        old_hash = to_be_deleted[file_path]
        path = Path(file_path)
        if old_hash is not None:
            try:
                if old_hash.refreshed(path) != old_hash:
                    continue
            except HashError:
                # The path can no longer be hashed, e.g. because it is a directory now,
                # so there is no way to tell whether it still holds the built content.
                await reporter("WARNING", f"Not removing {path}: it cannot be hashed.")
                continue
        if _try_remove(path.remove):
            await reporter("REMOVE", path)

    # Directories come after the files, so a directory that just lost its last file is empty
    # by the time it is considered for removal.
    dirs = {Path(path).normpath() for path in to_be_deleted if path.endswith(os.sep)}
    await _prune_empty_dirs(dirs, reporter)


async def _prune_empty_dirs(dirs: set[Path], reporter: ReporterClient):
    """Remove the directories in `dirs`, and their own parents, as long as they are empty.

    Parameters
    ----------
    dirs
        The directories to consider for removal.
    reporter
        Every removed directory is reported to this reporter.
    """
    # The sorted list is used as a stack, so the deepest directory is handled first.
    # The parent of a removed directory is pushed on top, i.e. out of sorted order,
    # so that the walk up towards the root continues right away.
    todo = sorted(dirs)
    while len(todo) > 0:
        path = todo.pop()
        if path.is_dir() and not any(path.iterdir()) and _try_remove(path.rmdir):
            await reporter("REMOVE", path)
            parent = path.parent
            if parent.name not in ("..", ".", ""):
                todo.append(parent)


def _try_remove(remove: Callable[[], None]) -> bool:
    """Call `remove` and return whether it succeeded.

    Any `OSError`, including `FileNotFoundError`, means "nothing was removed":
    the path may have been deleted or replaced by something else since the workflow
    recorded it, which is not worth interrupting the cleanup for.
    """
    try:
        remove()
    except OSError:
        return False
    return True
