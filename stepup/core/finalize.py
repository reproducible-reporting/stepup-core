# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Reporting and cleanup at the end of a build phase.

`Builder.finalize` (in `builder.py`) calls, in this order:

- `report_completion`, which reports whatever could not be built and derives a `ReturnCode`,
- `revert_optional`, which puts optional steps back to `PENDING`,
- `remove_outdated_outputs`, which deletes files that are no longer built by any step.

The last two form the cleanup pass and are skipped together,
e.g. after an incomplete build.

None of these touch `Builder` state:
they work with the workflow, the scheduler and the reporter only,
which is why they live here instead of in `builder.py`.
"""

from collections.abc import Callable, Iterable
from itertools import groupby

from path import Path

from .enums import FileState, Need, ReturnCode, StepState
from .hash import FileHash
from .pending import PendingSummary, analyze_pending
from .reporter import ReporterClient
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .workflow import GlobViolation, Workflow

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
        to_be_deleted = {
            row[0]: None if row[1] == FileState.VOLATILE.value else FileHash.from_json(row[2])
            for row in cur
        }
        if len(to_be_deleted) > 0:
            # Mark the files for deletion and reset their state in the database.
            workflow.to_be_deleted.update(to_be_deleted)
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


STATE_COLUMN = 20
"""Column at which the state (or resource name) field of a report line ends.

Every block on the page aligns its state (or resource name) on this column,
whatever the width of the block's label,
so that the paths (or resource units) after it line up as well.
"""

PENDING_INPUT_REMEDY = """\
Create the input file(s) listed above, correct their static() paths,
or add a step that builds them."""

PENDING_DETACHED_REMEDY = """\
Paths in parentheses are detached: no step in the workflow declares them."""

PENDING_RESOURCE_REMEDY = """\
Increase resources with --resources or lower the step requirements."""

PENDING_BROWSE_REMEDY = """\
Run `stepup browse` and search for a name above to see the steps involved."""


def _table_lines(rows: list[tuple[str, str, str]], dw: int, cw: int) -> list[str]:
    """Format `rows` as a block of aligned `Unavailable inputs` / `Blocked resources` lines.

    Parameters
    ----------
    rows
        `(key, detail, count)` triples, where **key** is a `FileState` or resource name
        (or `""` for a remainder row), right-aligned on `STATE_COLUMN`,
        **detail** is left-aligned on `dw`,
        and **count** is right-aligned on `cw` and followed by the literal `" step(s)"`.
    dw
        The width to left-align `detail` on.
    cw
        The width to right-align `count` on.

    Returns
    -------
    lines
        One line per row.
    """
    return [
        f"{key:>{STATE_COLUMN}s}: {detail:<{dw}s}  {count:>{cw}s} step(s)"
        for key, detail, count in rows
    ]


def _input_rows(summary: PendingSummary) -> list[tuple[str, str, str]]:
    """Build the `(state, path, count)` rows of the `Unavailable inputs` table."""
    rows = [
        (row.state.name, f"({row.path})" if row.detached else row.path, str(row.nblocked))
        for row in summary.inputs
    ]
    if summary.ninputs_hidden > 0:
        rows.append(
            (
                "",
                f"... and {summary.ninputs_hidden} more input(s)",
                f"≥ {summary.ninputs_hidden_blocked}",
            )
        )
    return rows


def _format_units_available(units_available: int | None) -> str:
    """Format the "available" clause of a `Insufficient resources` row."""
    return "none available" if units_available is None else f"{units_available} available"


def _resource_rows(summary: PendingSummary) -> list[tuple[str, str, str]]:
    """Build the `(name, detail, count)` rows of the `Insufficient resources` table."""
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
                f"≥ {summary.nresources_hidden_blocked}",
            )
        )
    return rows


def _other_lines(summary: PendingSummary) -> list[str]:
    """Format the `Other reasons` page: one prose line per non-empty bucket.

    The `, e.g. {example}` clause is always included:
    `PendingOther.example` is `None` only when `nblocked == 0`,
    which the queries in `pending.py` make impossible to reach here, so it is asserted
    rather than branched around.
    """
    lines = []
    for bucket, template in (
        (summary.failed, "{n} step(s) are blocked by failed steps, e.g. {example}."),
        (summary.cyclic, "{n} step(s) are waiting on each other, e.g. {example}."),
        (
            summary.deferred,
            "{n} step(s) are deferred with unavailable dynamic inputs, e.g. {example}.",
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
        assert bucket.example is not None
        lines.append(template.format(n=bucket.nblocked, example=bucket.example))
    return lines


def _remedy_lines(summary: PendingSummary) -> list[str]:
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


async def _report_pending_steps(
    db: DBSession, workflow: Workflow, reporter: ReporterClient
) -> ReturnCode:
    """Report a fixed-size root-cause summary of the steps that remained pending, if any."""
    async with db:
        summary = analyze_pending(workflow)
    if summary.ntotal == 0:
        return ReturnCode(0)

    input_rows = _input_rows(summary)
    resource_rows = _resource_rows(summary)
    table_rows = input_rows + resource_rows
    dw = max((len(detail) for _, detail, _ in table_rows), default=0)
    cw = max((len(count) for _, _, count in table_rows), default=0)

    pages = []
    if len(input_rows) > 0:
        pages.append(("Unavailable inputs", "\n".join(_table_lines(input_rows, dw, cw))))
    if len(resource_rows) > 0:
        pages.append(("Insufficient resources", "\n".join(_table_lines(resource_rows, dw, cw))))
    other_lines = _other_lines(summary)
    if len(other_lines) > 0:
        pages.append(("Other reasons", "\n".join(other_lines)))
    if len(pages) > 0:
        # Unconditional once any other page is present: the `stepup browse` line always
        # applies, and PENDING_INPUT_REMEDY/PENDING_RESOURCE_REMEDY are gated above on the
        # very tables that make this page non-empty in the first place.
        pages.append(("Remedy", "\n".join(_remedy_lines(summary))))

    await reporter("WARNING", f"{summary.ntotal} step(s) remained pending.", pages)
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
        # warning above by design (best-effort semantics), see Workflow.has_regular_output_under.
        missing_target_dirs = sorted(
            target_dir
            for target_dir in workflow.target_dirs
            if not workflow.has_regular_output_under(target_dir)
        )
    return_code = ReturnCode(0)
    if len(missing_targets) > 0:
        await reporter(
            "WARNING",
            f"{len(missing_targets)} target(s) are not produced by any step in the workflow: "
            + ", ".join(missing_targets),
        )
        return_code |= ReturnCode.WARNING
    if len(missing_target_dirs) > 0:
        await reporter(
            "WARNING",
            f"{len(missing_target_dirs)} directory target(s) matched no regular output "
            "in the workflow: " + ", ".join(missing_target_dirs),
        )
        return_code |= ReturnCode.WARNING
    return return_code


GLOB_VIOLATION_REMEDY = """\
Every matched file must be declared static or lie inside a static tree.
A matched directory also qualifies when it contains a static file or tree.
Declare the files with static(), or declare their directory as a static tree."""


def _format_glob_violation_state(state: FileState | None) -> str:
    """Format the state column of a `GlobViolation` row: the state name, or `(no node)`."""
    return "(no node)" if state is None else state.name


def _glob_violation_pages(violations: Iterable[GlobViolation]) -> list[tuple[str, str]]:
    """Format one page per (step, pattern) group of `violations`, aligned on `STATE_COLUMN`."""
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


async def _report_glob_matches(
    db: DBSession, workflow: Workflow, reporter: ReporterClient
) -> ReturnCode:
    """Report glob matches that no static declaration justifies."""
    async with db:
        violations = workflow.find_glob_violations()
    returncode = ReturnCode(0)
    warnings = [violation for violation in violations if not violation.is_error]
    errors = [violation for violation in violations if violation.is_error]
    if len(warnings) > 0:
        returncode |= ReturnCode.WARNING
        pages = _glob_violation_pages(warnings)
        pages.append(("Remedy", GLOB_VIOLATION_REMEDY))
        await reporter("WARNING", f"{len(warnings)} glob match(es) are not declared static.", pages)
    if len(errors) > 0:
        returncode |= ReturnCode.FAILED
        await reporter(
            "ERROR",
            f"{len(errors)} glob match(es) are files that a step builds.",
            _glob_violation_pages(errors),
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

    returncode |= await _report_pending_steps(db, workflow, reporter)
    returncode |= await _report_missing_targets(db, workflow, reporter)
    # Late glob validation is skipped when the build already went wrong: an unjustified
    # match is then usually a consequence (a plan that would have declared it never ran),
    # and fixing the real failure tends to fix this too.
    if returncode == ReturnCode(0):
        returncode |= await _report_glob_matches(db, workflow, reporter)
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
    # Remove the files from the file system, deepest first, so a directory is only
    # pruned once every file it contains is gone.
    parents = set()
    for path, file_hash in sorted(workflow.to_be_deleted.items(), reverse=True):
        path = Path(path)
        if (file_hash is None or file_hash.regen(path) == file_hash) and _try_remove(path.remove):
            await reporter("REMOVE", path)
            parents.add(path.parent)

    await _prune_empty_dirs(parents, reporter)

    # Reset the state of the deleted files in the database,
    # if they are still present and not VOLATILE.
    #
    # The VOLATILE guard is what makes the one in UPDATE_OPTIONAL_TO_BE_DELETED stick:
    # `revert_optional` runs earlier in the same `Builder.finalize`,
    # and the volatile paths it flagged for deletion still have a live file row here.
    async with db:
        db.executemany(
            """
            WITH node_tmp AS (SELECT i FROM node WHERE label = ?)
            UPDATE file
            SET state = ?, hash = NULL
            WHERE node IN node_tmp
            AND state != ?
            """,
            [
                (path, FileState.PLANNED.value, FileState.VOLATILE.value)
                for path in workflow.to_be_deleted
            ],
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
