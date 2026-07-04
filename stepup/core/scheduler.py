# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Scheduler` Turns PENDING jobs into RUNNING jobs as the builder requests them."""

import logging
import time

import attrs

from .enums import FileState, Need, StepState
from .hash import FileHash
from .job import Job, RunJob, ValidateAmendedJob
from .sqlite3 import DBSession
from .step import Step
from .utils import parse_resources
from .workflow import Workflow

logger = logging.getLogger(__name__)

# Update the _safe metadata field for all flagged steps in the workflow.
RECURSIVE_UPDATE_SAFE = f"""
UPDATE step SET _safe = cte.safe FROM (
    -- The trace table indicates which creators are in a safe state.
    -- Nodes who have a _safe creator should be flagged with _safe = 1,
    -- and _safe = 0 otherwise.
    WITH RECURSIVE trace(i, safe) AS (
        -- Start with all steps whose _check_safe is set.
        SELECT
            s.node,
            s.state in (
                {StepState.RUNNING.value}, {StepState.CHECKING.value}, {StepState.SUCCEEDED.value}
            )
        FROM step AS s
        WHERE _check_safe

        UNION ALL

        -- Iterate over all their (recursive) products, and set safe to 0 if the creator is not safe
        -- or its own state does not allow queuing of products.
        SELECT
            s.node,
            trace.safe AND s.state in (
                {StepState.RUNNING.value}, {StepState.CHECKING.value}, {StepState.SUCCEEDED.value}
            )
        FROM trace
        JOIN node AS product ON trace.i = product.creator
        JOIN step AS s ON product.i = s.node
    )
    -- Transfer the safe state of the creators to the product nodes.
    SELECT node.i, trace.safe
    FROM node
    JOIN trace ON trace.i = node.creator
) as cte
WHERE step.node = cte.i
"""


INIT_CHECK_AFTER = """
CREATE TEMPORARY TABLE IF NOT EXISTS check_after(i INTEGER PRIMARY KEY)
"""


EMPTY_CHECK_AFTER = """
DELETE FROM check_after
"""


# Don't bother updating _check_after for detached steps.
PRUNE_DETACHED_CHECK_AFTER = """
INSERT INTO check_after(i) SELECT step.node FROM step JOIN node ON step.node = node.i
WHERE NOT node.detached AND step._check_after
"""


# Remove from check_after steps that have (indirect) sinks also in check_after.
# This is done by starting from the flagged steps, and recursively following their sources,
# until we hit another step whose _check_after is set.
# Such source steps will be updated anyway, and unchecking them here
# will avoid duplicate work and potential conflicts later.
PRUNE_REDUNDANT_CHECK_AFTER = """
DELETE FROM check_after
WHERE i IN (
    WITH RECURSIVE trace(i, depth, hit) AS (
        -- Start from all steps in check_after
        SELECT
            i,
            0,
            FALSE
        FROM check_after
        UNION ALL
        -- Iterate over all their (recursive) sources,
        -- until we hit another step whose _check_after is set.
        -- There is no need to go further,
        -- because that step will be updated elsewhere in the trace.
        SELECT
            source_step.node,
            trace.depth + 1,
            source_step._check_after
        FROM trace
        JOIN dependency AS dep1 ON dep1.sink = trace.i
        JOIN dependency AS dep2 ON dep2.sink = dep1.source
        JOIN step AS source_step ON dep2.source = source_step.node
        JOIN node AS source_node ON source_step.node = source_node.i
        WHERE NOT source_node.detached AND NOT trace.hit
    )
    SELECT trace.i
    FROM trace
    WHERE trace.depth > 0
)
"""


DROP_UPDATE_CHECK_AFTER = """
DROP TABLE IF EXISTS update_after
"""


INIT_UPDATE_CHECK_AFTER = """
CREATE TEMP TABLE update_after(
    i INTEGER PRIMARY KEY,
    _implied_need INTEGER,
    _tail_time REAL
)
"""


# Compute the new _implied_need and _tail_time for each step in check_after.
SELECT_UPDATE_CHECK_AFTER = f"""
WITH cte AS (
    SELECT
        check_after.i AS i,
        step._implied_need AS old_implied_need,
        MAX(
            step.need,
            COALESCE(
                MAX(sink_step._implied_need),
                {Need.OPTIONAL.value}
            )
        ) AS new_implied_need,
        step._tail_time AS old_tail_time,
        (
            step.duration
            + COALESCE(MAX(sink_step._tail_time), 0)
        ) AS new_tail_time
    FROM check_after
    JOIN step ON step.node = check_after.i
    LEFT JOIN dependency AS dep1 ON dep1.source = check_after.i
    LEFT JOIN dependency AS dep2 ON dep2.source = dep1.sink
    LEFT JOIN node AS sink_node ON (
        sink_node.i = dep2.sink
        AND NOT sink_node.detached
    )
    LEFT JOIN step AS sink_step ON (
        sink_step.node = sink_node.i
    )
    GROUP BY check_after.i
)
INSERT INTO update_after(i, _implied_need, _tail_time)
SELECT i, new_implied_need, new_tail_time
FROM cte
WHERE :first OR (new_implied_need != old_implied_need OR new_tail_time != old_tail_time)
"""


# Apply the updates
APPLY_UPDATE_CHECK_AFTER = """
UPDATE step SET
    _implied_need = update_after._implied_need,
    _tail_time = update_after._tail_time
FROM update_after
WHERE step.node = update_after.i
"""


# Propagate the updates to all (recursive) sources of the updated steps.
PROPAGATE_UPDATE_CHECK_AFTER = """
INSERT INTO check_after(i)
SELECT DISTINCT source_step.node
FROM update_after
JOIN dependency AS dep1 ON dep1.sink = update_after.i
JOIN dependency AS dep2 ON dep2.sink = dep1.source
JOIN step AS source_step ON source_step.node = dep2.source
JOIN node AS source_node ON source_step.node = source_node.i
WHERE NOT source_node.detached
"""


DROP_CHECK_AFTER = """
DROP TABLE IF EXISTS check_after;
"""


# Subquery body for EXISTS checks: matches input files that block a step from running.
# The amended_dep data is brought in via LEFT JOIN
# to distinguish between initial and amended dependencies.
UNAVAILABLE_INPUT = f"""
SELECT node.i
FROM dependency AS dep
JOIN file AS input_file ON input_file.node = dep.source
JOIN node AS input_node ON input_node.i = dep.source
LEFT JOIN amended_dep ON amended_dep.i = dep.i
WHERE dep.sink = node.i AND (
    input_file.state = {FileState.VOLATILE.value} OR
    (
        -- Case 1: Is an amended dependency
        amended_dep.i IS NOT NULL AND
        NOT input_node.detached AND
        input_file.state IN ({FileState.AWAITED.value}, {FileState.OUTDATED.value})
    ) OR
    (
        -- Case 2: Is an initial dependency
        amended_dep.i IS NULL AND
        (
            input_node.detached OR
            input_file.state NOT IN ({FileState.BUILT.value}, {FileState.STATIC.value})
        )
    )
)
"""


# Building blocks shared by step-selection queries.
_PENDING_STEP_WHERE = f"""step.state = {StepState.PENDING.value} AND
    step._safe AND
    step.unavailable_inputs = '' AND
    step._implied_need > {Need.OPTIONAL.value} AND
    NOT node.detached"""

# Priority WHERE clause:
# - Planning steps run first to unlock more work early.
# - Within each group, higher tail_time steps go first.
#   A step that has been rescheduled (multiple times) gets its tail_time divided by
#   1 + reschedule_count, to reduce the risk of too early dispatching after rescheduling.
#   Dividing (rather than subtracting a fixed penalty) keeps the demotion proportional
#   to the step's own tail_time, so it behaves consistently regardless of the time
#   scale of the workflow.
# - Label provides a deterministic tie-breaker.
_ORDER_BY_PRIORITY = f"""ORDER BY
    (step._implied_need = {Need.PLAN.value}) DESC,
    step._tail_time / (1 + step.reschedule_count) DESC,
    node.label ASC"""


# Select the highest priority PENDING step that can be hash-checked for possible skipping.
# Unlike SELECT_RUNNABLE_STEPS, this query:
# - requires a stored hash (a row in step_hash), because a hash is needed to check for skipping
# - does NOT check resource availability, as hash checking and skipping never needs named resources
SELECT_CHECKABLE_STEPS = f"""
SELECT
    node.i AS i,
    node.label AS label
FROM node
JOIN step ON node.i = step.node
WHERE
    {_PENDING_STEP_WHERE} AND
    NOT EXISTS ({UNAVAILABLE_INPUT}) AND
    EXISTS (SELECT 1 FROM step_hash WHERE step_hash.node = node.i)
{_ORDER_BY_PRIORITY}
"""


# Select the highest priority PENDING step that is ready to be executed.
SELECT_RUNNABLE_STEPS = f"""
SELECT
    node.i AS i,
    node.label AS label
FROM node
JOIN step ON node.i = step.node
WHERE
    {_PENDING_STEP_WHERE} AND
    NOT EXISTS ({UNAVAILABLE_INPUT}) AND
    NOT EXISTS (
        -- Exclude the step if any required resource is undefined or over-committed.
        SELECT 1 FROM step_resource AS req
        LEFT JOIN available_resource AS avail ON avail.name = req.name
        WHERE req.node = node.i
          AND (
              avail.name IS NULL
              OR (
                  avail.units
                  - COALESCE((
                      SELECT SUM(r2.units)
                      FROM step_resource AS r2
                      JOIN step AS s2 ON s2.node = r2.node
                      WHERE r2.name = req.name
                        AND s2.state = {StepState.RUNNING.value}
                  ), 0)
              ) < req.units
          )
    )
{_ORDER_BY_PRIORITY}
"""


# Select the input hashes and metadata for a given step.
SELECT_INPUTS = """
SELECT
    node.label,
    node.detached,
    file.state,
    EXISTS (SELECT 1 FROM amended_dep WHERE amended_dep.i = dep.i),
    file.hash
FROM node JOIN dependency AS dep ON node.i = dep.source
JOIN file ON file.node = node.i
WHERE dep.sink = ?
"""


# Select the available and used resource counts for each resource.
SELECT_RESOURCE_COUNTS = f"""
SELECT ar.name, COALESCE(running.used, 0) AS used, ar.units AS available
FROM available_resource AS ar
LEFT JOIN (
    SELECT st.name, SUM(st.units) AS used
    FROM step_resource AS st
    JOIN step AS s ON s.node = st.node
    WHERE s.state = {StepState.RUNNING.value}
    GROUP BY st.name
) AS running ON running.name = ar.name
"""


# Identify the reasons why pending steps are not runnable after the builder has stopped.
# It is assumed that there are no RUNNING steps at this point.
# (This is typically called after the builder has (been) stopped.)
SELECT_PENDING_REASONS = f"""
SELECT
    node.i,
    node.label,
    step._safe,
    (step.unavailable_inputs != '' OR step.unfresh_inputs != '') AS rescheduled,
    EXISTS ({UNAVAILABLE_INPUT}) AS hasUNAVAILABLE_INPUTs,
    EXISTS (
        SELECT 1 FROM step_resource AS req
        LEFT JOIN available_resource AS avail ON avail.name = req.name
        WHERE req.node = node.i AND (avail.name IS NULL OR avail.units < req.units)
    ) AS has_resource_issue
FROM node
JOIN step ON node.i = step.node
WHERE step.state = {StepState.PENDING.value} AND
    step._implied_need != {Need.OPTIONAL.value} AND
    NOT node.detached
{_ORDER_BY_PRIORITY}
"""


@attrs.define
class Scheduler:
    """Turn PENDING jobs into RUNNING jobs as the builder requests them."""

    workflow: Workflow = attrs.field()
    """The workflow that the scheduler is responsible for."""

    db: DBSession = attrs.field(kw_only=True)
    """Lock for workflow database access."""

    use_duration: bool = attrs.field(kw_only=True, default=False)
    """Whether to use the duration of steps to optimize the execution order."""

    on_hold: bool = attrs.field(init=False, default=False)
    """Temporarily pause scheduling of jobs, e.g. interrupted by the user."""

    start_times: dict[int, int] = attrs.field(init=False, factory=dict)
    """Step node id -> `time.monotonic_ns()` at the moment it was dispatched to RUNNING.

    In-memory only (not persisted): used by `amend()`'s freshness check to compare
    against a producer's `stop_times` entry.
    """

    stop_times: dict[int, int] = attrs.field(init=False, factory=dict)
    """Step node id -> `time.monotonic_ns()` at the moment it reached SUCCEEDED.

    In-memory only, pruned by `job_finished()`. See `start_times`.
    """

    #
    # Initialization
    #

    async def set_available_resources(self, resources: str | None):
        async with self.db:
            self.workflow.db.execute(
                "CREATE TEMPORARY TABLE IF NOT EXISTS available_resource "
                "(name TEXT PRIMARY KEY, units INTEGER NOT NULL)"
            )
            self.workflow.db.execute("DELETE FROM available_resource")
            if resources is not None:
                self.workflow.db.executemany(
                    "INSERT INTO available_resource VALUES (?, ?)",
                    parse_resources(resources).items(),
                )

    #
    # Interaction with builder
    #

    async def pop_runnable_job(self) -> Job | None:
        if self.on_hold:
            logger.debug("Scheduler is on hold, not popping any jobs")
            return None

        # We're taking a rather long lock here,
        # but this is needed because subsequent
        # changes to the database are correlated.
        # Allowing database changes in between would result
        # in potential race conditions and inconsistencies.
        async with self.db:
            # A) Perform metadata updates for all steps whose changes have not been propagated
            #    into the metadata columns yet.

            # The metadata checks are flagged by _check_* columns in the step table,
            # which are set to True when something relevant in the step has changed.
            self._update_meta_safe()
            self._update_meta_after()

            # B) Identify the highest priority PENDING step that is ready for execution.
            #    Checkable steps (those with a stored hash) are scheduled first without a
            #    resource check, because hash-checking never needs named resources.
            #    Executable steps (no stored hash) are scheduled next, subject to resources.
            step = self._get_step(SELECT_CHECKABLE_STEPS)
            if step is not None:
                job = self._derive_job(step)
                logger.debug("Derived checkable job: %s", job)
                logger.info("Pop %s", job.name)
                step.set_state(StepState.CHECKING)
                step.clear_unavailable_inputs()
                # Not that skip jobs record not start time, because skipping does not produce
                # new outputs, and thus have no risk of race conditions related to amend()'s
                # freshness check.
                return job
            step = self._get_step(SELECT_RUNNABLE_STEPS)
            if step is None:
                logger.debug("No runnable steps found")
                return None
            logger.debug("Queueing step %s", step)
            job = self._derive_job(step)
            logger.debug("Derived job: %s", job)
            logger.info("Pop %s", job.name)
            step.set_state(StepState.RUNNING)
            self.start_times[step.i] = time.monotonic_ns()
            step.clear_unavailable_inputs()
            return job

    def job_finished(self, step_i: int, *, succeeded: bool) -> None:
        """Update start/stop-time bookkeeping after a step reaches a terminal state.

        Parameters
        ----------
        step_i
            The node id of the step that just completed.
        succeeded
            Whether the step reached SUCCEEDED (True) or PENDING/FAILED (False).
        """
        self.start_times.pop(step_i, None)
        if succeeded:
            self.stop_times[step_i] = time.monotonic_ns()
        if self._count_running() == 0:
            # Nothing running means nothing can race with a not-yet-dispatched step,
            # so every remaining stop_times entry is safe to drop.
            self.stop_times.clear()

    def _count_running(self) -> int:
        row = self.workflow.db.execute(
            "SELECT COUNT(*) FROM step WHERE state = ?", (StepState.RUNNING.value,)
        ).fetchone()
        return row[0]

    def _update_meta_safe(self):
        """Update the "safe" metadata fields where needed."""
        db = self.workflow.db
        cur = db.execute(RECURSIVE_UPDATE_SAFE)
        logger.debug(f"Updated {cur.rowcount} _safe metadata field(s) for steps")
        cur = db.execute("UPDATE step SET _check_safe = 0 WHERE _check_safe")
        logger.debug(f"Updated {cur.rowcount} _check_safe metadata field(s) for steps")

    def _update_meta_after(self):
        """Update the "after" metadata fields where needed."""
        db = self.workflow.db
        # Not using executescript to preserve atomicity of the transaction.
        db.execute(INIT_CHECK_AFTER)
        db.execute(EMPTY_CHECK_AFTER)
        db.execute(PRUNE_DETACHED_CHECK_AFTER)
        db.execute(PRUNE_REDUNDANT_CHECK_AFTER)
        ncheck = db.execute("SELECT COUNT(*) FROM check_after").fetchone()[0]
        first = True
        while ncheck > 0:
            logger.debug(f"Found {ncheck} sources to update (first={first})")
            # for row in db.execute("SELECT i FROM check_after"):
            #     logger.debug("  Step %s", row[0])
            # The first iteration is different: irrespective of having changed metadata fields of
            # the initial _check_after steps, we need to propagate at least once.
            db.execute(DROP_UPDATE_CHECK_AFTER)
            db.execute(INIT_UPDATE_CHECK_AFTER)
            db.execute(SELECT_UPDATE_CHECK_AFTER, {"first": first})
            # for row in db.execute("SELECT * FROM update_after"):
            #     logger.debug("  Updating step %s", str(row))
            db.execute(APPLY_UPDATE_CHECK_AFTER)
            db.execute(EMPTY_CHECK_AFTER)
            db.execute(PROPAGATE_UPDATE_CHECK_AFTER)
            ncheck = db.execute("SELECT COUNT(*) FROM check_after").fetchone()[0]
            first = False
        logger.debug("Finished updating 'after' metadata fields")
        db.execute(DROP_CHECK_AFTER)
        cur = db.execute("UPDATE step SET _check_after = 0 WHERE _check_after")
        logger.debug(f"Updated {cur.rowcount} _check_after metadata field(s) for steps")

    def _get_step(self, sql: str) -> Step | None:
        row = self.workflow.db.execute(sql).fetchone()
        if row is None:
            return None
        i, label = row
        return Step(self.workflow, i, label)

    def _derive_job(self, step: Step) -> RunJob | ValidateAmendedJob:
        """Derive a Job instance for a step that is ready to be queued."""
        amended_inputs_ready = True
        inp_hashes = []
        db = self.workflow.db
        cur = db.execute(SELECT_INPUTS, (step.i,))
        for path, detached, fs_value, is_amended, hash_value in cur:
            # All exception cases handled in this loop should have been filtered out
            # by the SELECT_INPUTS query.
            # We keep them here as sanity checks, because they indicate a serious internal error.
            file_state = FileState(fs_value)

            # Pre-flight sanity check
            if file_state == FileState.VOLATILE:
                # Volatile files should never be selected for queueing,
                # as they are not even allowed as inputs.
                raise RuntimeError(
                    f"Step {step} has a volatile input {path}, but is selected for queueing"
                )

            # Amended or not, just process ready inputs.
            if not detached and file_state in (FileState.BUILT, FileState.STATIC):
                # Input is ready, collect its hash and look no further.
                inp_hashes.append((path, FileHash.from_json(hash_value)))
                continue

            # Sanity checks
            if is_amended:
                if not detached and file_state in (FileState.AWAITED, FileState.OUTDATED):
                    # Attached amended inputs with state AWAITED or OUTDATED are not ready.
                    # This should never have been selected for queueing.
                    raise RuntimeError(
                        f"Step {step} has an amended input {path} that is not ready yet, "
                        f"but is in an unexpected state {file_state}"
                    )
            else:
                # Initial input not ready, which should never have been selected for queueing.
                raise RuntimeError(
                    f"Step {step} has an initial input {path} that is not ready yet, "
                    f"but is in an unexpected state {file_state}"
                )

            # If we reach this code path, the current input is amended and
            # (1) is detached or (2) has state MISSING.
            # In this case, we request to validate amended inputs first.
            # This means that amended inputs will be discarded and rederived again
            # if any of the initial or available amended inputs have changed.
            amended_inputs_ready = False

        # Get the current step hash, which is used to determine whether the step can be skipped.
        step_hash = step.get_hash()
        # Get a list of environment variables used, as these are needed to compute the new hash.
        env_deps = list(step.env_deps())

        if amended_inputs_ready or step_hash is None:
            # All (amended) inputs are ready, or the job is not skippable.
            # When there is a hash, this will check if any inputs have changed since the last run,
            # and skip the job if not.
            # In all other cases, the job will be executed without skipping,
            # and the step hash will be updated after completion.
            return RunJob(step, inp_hashes, env_deps, step_hash)
        # If the initial inputs are ready, but the amended inputs are not,
        # and there is a step hash, we need to validate the amended inputs first.
        # If they are not available, and if the existing inputs have changed,
        # they may also no longer be needed.
        return ValidateAmendedJob(step, inp_hashes, env_deps, step_hash)

    async def job_completed(self, job):
        """Handle a completed job, which does not do anything for the moment."""
        if self.use_duration:
            async with self.db:
                job.step.set_duration(job.duration())
        logger.info("Done %s", job.name)

    #
    # Information gathering (must be wrapped in db by caller)
    #

    def get_resource_counts(self) -> dict[str, dict[str, int]]:
        """Return used and available resource counts."""
        db = self.workflow.db
        result = {}
        for row in db.execute(SELECT_RESOURCE_COUNTS):
            name, used, available = row
            result[name] = {"used": used, "available": available}
        return result

    def get_pending_step_records(self) -> list[tuple["Step", str]]:
        """Return non-optional pending steps with reasons why each could not be executed.

        Must be called after the builder has stopped (no steps in RUNNING state).

        Returns
        -------
        list[tuple[Step, str]]
            Each tuple contains a Step and a reason string, one of:

            - `runnable`: step seems runnable but was not executed
              (e.g. the builder was interrupted before reaching it)
            - `inputs`: required inputs are unavailable
              (detached, wrong file state, or waiting for amended inputs)
            - `resources`: required resources exceed the maximum available
            - `unsafe`: the step's creator is not RUNNING or SUCCEEDED
        """
        results = []
        cur = self.workflow.db.execute(SELECT_PENDING_REASONS)
        for i, label, safe, rescheduled, unavailable_inputs, resource_issue in cur:
            step = Step(self.workflow, i, label)
            if not safe:
                reason = "unsafe"
            elif rescheduled or unavailable_inputs:
                reason = "inputs"
            elif resource_issue:
                reason = "resources"
            else:
                reason = "runnable"
            results.append((step, reason))
        return results
