# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Scheduler` Turns PENDING jobs into RUNNING jobs as the builder requests them."""

import logging
import time

import attrs

from .enums import FileState, Need, StepState
from .hash import FileHash
from .job import Job, RunJob, ValidateAmendedJob
from .path import dir_range_upper
from .sqlite3 import DBSession
from .step import STEP_DISPATCH_WHERE, Step
from .utils import parse_resources, write_joblog_record
from .workflow import Workflow

__all__ = ("Scheduler",)

logger = logging.getLogger(__name__)


INIT_SAFE_UPDATE = """
CREATE TEMP TABLE IF NOT EXISTS safe_update(i INTEGER PRIMARY KEY, safe INTEGER, safe_nh INTEGER)
"""


EMPTY_SAFE_UPDATE = """
DELETE FROM safe_update
"""


# Compute the new _safe and _safe_ignoring_hold values for every (recursive) product of a
# _check_safe-flagged step, AND for the flagged step itself.
#
# A product node can be reached through more than one flagged ancestor at once (e.g.
# Step.detach()/recycle() flags a whole subtree via RECURSIVE_CHECK_WITH_PRODUCTS in step.py), so
# duplicate rows for the same node id are possible and are resolved with MIN(safe)/MIN(safe_nh):
# the value derived through a longer (more ancestor-inclusive) chain is always <= the value from
# a shorter chain, so MIN always recovers the correct, fully-chained answer rather than an
# arbitrary one.
#
# `trace` carries four values per node: `safe`/`safe_nh` are that node's own new
# _safe/_safe_ignoring_hold (what gets written out) and depend only on its *ancestors'*
# states -- never its own, since a step's own state must not affect its own _safe (e.g.
# `step_dispatch`'s partial index checks _safe against PENDING steps, so folding this step's
# own state into _safe would make every PENDING step look permanently unsafe). `chain`/
# `chain_nh` are `safe`/`safe_nh` with this node's own state additionally folded in, i.e.
# exactly what its products need as their incoming ancestor-safety. Computing the "_nh"
# ("no hold") twin costs nothing extra beyond the additional arithmetic: it walks the exact
# same rows as `safe`/`chain`, just without ever consulting `_holding`, so no new traversal,
# join, or index is needed for it. `chain` costs nothing extra either: the join that looks up
# a node's own state (`sp`) is already needed to identify the node itself, so all four values
# fall out of the same row instead of requiring a second downward pass (as an earlier version
# of this query did, broadcasting `safe` from creators to products via a separate final CROSS
# JOIN).
SELECT_SAFE_UPDATE = f"""
INSERT INTO safe_update(i, safe, safe_nh)
WITH RECURSIVE trace(i, safe, chain, safe_nh, chain_nh) AS (
    -- Seed directly at each _check_safe-flagged step, using its creator's already-computed
    -- _safe/_safe_ignoring_hold and state (a root creator has no `step` row and is treated as
    -- trivially safe via COALESCE). creator_step._holding = 0 additionally excludes, for the
    -- `safe`/`chain` (hold-respecting) pair only, a step whose creator has one or more open
    -- `hold()` calls, so its children stay unsafe until the matching `release()` brings the
    -- counter back to zero. The `safe_nh`/`chain_nh` pair mirrors this exactly but never
    -- consults `_holding` at all, i.e. what `_safe` would be if nothing were ever held.
    SELECT
        s.node,
        COALESCE(
            creator_step._safe AND
                creator_step.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value}) AND
                creator_step._holding = 0,
            1
        ),
        COALESCE(
            creator_step._safe AND
                creator_step.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value}) AND
                creator_step._holding = 0,
            1
        ) AND s.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value})
            AND s._holding = 0,
        COALESCE(
            creator_step._safe_ignoring_hold AND
                creator_step.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value}),
            1
        ),
        COALESCE(
            creator_step._safe_ignoring_hold AND
                creator_step.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value}),
            1
        ) AND s.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value})
    FROM step AS s
    JOIN node AS cnode ON cnode.i = s.node
    LEFT JOIN step AS creator_step ON creator_step.node = cnode.creator
    WHERE s._check_safe

    UNION ALL

    -- Follow (recursive) products: a product's own new safe/safe_nh is simply the
    -- chain/chain_nh inherited from its creator. Both are refreshed in the same row to also
    -- fold in the product's own state (and, for chain only, its own _holding), ready for use
    -- by *its* products in the next iteration.
    SELECT
        sp.node,
        trace.chain,
        trace.chain AND sp.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value})
            AND sp._holding = 0,
        trace.chain_nh,
        trace.chain_nh AND sp.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value})
    FROM trace
    JOIN node AS product ON product.creator = trace.i
    JOIN step AS sp ON sp.node = product.i
)
SELECT i, MIN(safe), MIN(safe_nh) FROM trace GROUP BY i
"""


APPLY_SAFE_UPDATE = """
UPDATE step SET
    _safe = (SELECT safe FROM safe_update WHERE safe_update.i = step.node),
    _safe_ignoring_hold = (SELECT safe_nh FROM safe_update WHERE safe_update.i = step.node)
WHERE step.node IN (SELECT i FROM safe_update)
"""


INIT_CHECK_AFTER = """
CREATE TEMPORARY TABLE IF NOT EXISTS check_after(i INTEGER PRIMARY KEY)
"""


# Holds only the ids of steps whose _implied_need/_tail_time changed in the current
# _update_meta_after iteration -- see PROPAGATE_UPDATE_CHECK_AFTER for how it's used.
INIT_CHANGED_AFTER = """
CREATE TEMPORARY TABLE IF NOT EXISTS changed_after(i INTEGER PRIMARY KEY)
"""


EMPTY_CHECK_AFTER = """
DELETE FROM check_after
"""


# Don't bother updating _check_after for detached steps.
PRUNE_DETACHED_CHECK_AFTER = """
INSERT INTO check_after(i) SELECT step.node FROM step JOIN node ON step.node = node.i
WHERE NOT node.detached AND step._check_after
"""


# Compute the new _implied_need and _tail_time for each step in check_after, and apply them
# directly in the same statement (avoids a round trip through a separate, materialized
# update_after table). RETURNING reports exactly the node ids that were written, which the
# caller feeds into PROPAGATE_UPDATE_CHECK_AFTER as the "changed" seed set -- narrowing
# propagation to steps whose value actually changed, same as the old two-statement design.
#
# The CASE/EXISTS term elevates a step to at least TARGET when one of its attached,
# non-volatile outputs is a target. Dependency sinks of a step are exactly its out_paths
# (AWAITED/BUILT/OUTDATED) and vol_paths (VOLATILE), so excluding VOLATILE leaves exactly
# the regular outputs. NOT onode.detached mirrors Workflow.reconcile_targets()'s
# deliberate skipping of detached rows.
# Without targets, target_path (always created and populated in Scheduler.initialize())
# is empty, so the EXISTS never matches and the term contributes Need.OPTIONAL --
# the enum minimum, a no-op inside MAX. All probes in the EXISTS are indexed,
# so this costs little on untargeted builds.
#
# The second WHEN arm elevates a step whose *declared* need is DEFAULT (step.need, not
# _implied_need) when one of its attached, non-volatile outputs falls under a directory
# target (label in [target_dir.path, target_dir.upper)). The step.need = DEFAULT guard
# keeps directory targets from sweeping OPTIONAL steps into the build, unlike exact
# targets. Without directory targets, target_dir (always created and populated in
# Scheduler.initialize()) is empty, so this term is also a no-op.
UPDATE_CHECK_AFTER = f"""
WITH cte AS (
    SELECT
        check_after.i AS i,
        step._implied_need AS old_implied_need,
        MAX(
            step.need,
            CASE WHEN EXISTS (
                SELECT 1 FROM dependency AS depo
                JOIN node AS onode ON onode.i = depo.sink
                JOIN file AS ofile ON ofile.node = depo.sink
                WHERE depo.source = check_after.i
                  AND NOT onode.detached
                  AND ofile.state != {FileState.VOLATILE.value}
                  AND onode.label IN (SELECT path FROM target_path)
            ) THEN {Need.TARGET.value}
            WHEN step.need = {Need.DEFAULT.value} AND EXISTS (
                SELECT 1 FROM dependency AS depo
                JOIN node AS onode ON onode.i = depo.sink
                JOIN file AS ofile ON ofile.node = depo.sink
                WHERE depo.source = check_after.i
                  AND NOT onode.detached
                  AND ofile.state != {FileState.VOLATILE.value}
                  AND EXISTS (
                      SELECT 1 FROM target_dir
                      WHERE onode.label >= target_dir.path
                        AND onode.label < target_dir.upper
                  )
            ) THEN {Need.TARGET.value} ELSE {Need.OPTIONAL.value} END,
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
UPDATE step SET
    _implied_need = upd.new_implied_need,
    _tail_time = upd.new_tail_time
FROM (
    SELECT i, new_implied_need, new_tail_time
    FROM cte
    WHERE :first OR (new_implied_need != old_implied_need OR new_tail_time != old_tail_time)
) AS upd
WHERE step.node = upd.i
RETURNING step.node
"""


EMPTY_CHANGED_AFTER = """
DELETE FROM changed_after
"""


# Propagate the updates to all (recursive) sources of the updated steps.
#
# changed_after holds the step ids whose _implied_need/_tail_time actually changed in this
# iteration (populated by the caller from UPDATE_CHECK_AFTER's RETURNING output) -- this is the
# seed set that preserves the "only propagate from steps whose value actually changed" narrowing,
# which keeps iteration counts down on real graphs.
#
# The two nested `IN` subqueries (rather than a plain JOIN chain starting from `changed_after`)
# make the planner drive from `dependency`'s `dependency_sink_source` index seeded by
# `changed_after`, instead of a full scan of `dependency` or `step`. A plain JOIN here lets the
# planner pick either of those as the driving table, an O(n_dependencies) or O(n_steps) cost
# regardless of how few steps changed.
PROPAGATE_UPDATE_CHECK_AFTER = """
INSERT INTO check_after(i)
SELECT DISTINCT source_step.node
FROM dependency AS dep2
JOIN step AS source_step ON source_step.node = dep2.source
JOIN node AS source_node ON source_step.node = source_node.i
WHERE NOT source_node.detached
  AND dep2.sink IN (
      SELECT dep1.source FROM dependency AS dep1
      WHERE dep1.sink IN (SELECT i FROM changed_after)
  )
"""


# Subquery body for EXISTS checks: matches input files that block a step from running.
# The amended_dep data is brought in via LEFT JOIN
# to distinguish between initial and amended dependencies.
# `correlate` is the SQL expression identifying "this step's node id" in the enclosing
# query -- `node.i` when joined against `node`/`step` (SELECT_PENDING_REASONS), or
# `step.node` when there is no `node` table in scope (RECOMPUTE_READY, a bare
# `UPDATE step ...`). The two instantiations below share this one body so they can never
# drift apart.
def _unavailable_input_sql(correlate: str) -> str:
    # Only ever used inside EXISTS(...)/NOT EXISTS(...), so the projected column is
    # irrelevant to the result -- `SELECT 1` avoids depending on an outer `node` alias that
    # may not be in scope (e.g. RECOMPUTE_READY's bare `UPDATE step ...`).
    return f"""
    SELECT 1
    FROM dependency AS dep
    JOIN file AS input_file ON input_file.node = dep.source
    JOIN node AS input_node ON input_node.i = dep.source
    LEFT JOIN amended_dep ON amended_dep.i = dep.i
    WHERE dep.sink = {correlate} AND (
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


# Used by SELECT_PENDING_REASONS, correlated on the outer node.i.
UNAVAILABLE_INPUT = _unavailable_input_sql("node.i")


# Recompute step._ready for every _check_ready-flagged step. See Scheduler._update_meta_ready.
RECOMPUTE_READY = f"""
UPDATE step SET
    _ready = NOT EXISTS ({_unavailable_input_sql("step.node")}),
    _check_ready = 0
WHERE _check_ready
"""


# Priority WHERE clause:
# - Planning steps run first to unlock more work early.
# - Within each group, higher tail_time steps go first.
#   A step that has been postponed (multiple times) gets its tail_time divided by
#   1 + postpone_count, to reduce the risk of too early dispatching after postponing.
#   Dividing (rather than subtracting a fixed penalty) keeps the demotion proportional
#   to the step's own tail_time, so it behaves consistently regardless of the time
#   scale of the workflow.
# - Label provides a deterministic tie-breaker.
_ORDER_BY_PRIORITY = f"""ORDER BY
    (step._implied_need = {Need.PLAN.value}) DESC,
    step._tail_time / (1 + step.postpone_count) DESC,
    node.label ASC"""


# Whether a step has at least one required resource that is currently undefined
# or over-committed (i.e. cannot be run right now).
# Named resources are only relevant to actual execution,
# never to hash-checking -- see SELECT_NEXT_STEP.
# Not shared with SELECT_PENDING_REASONS's similarly-shaped resource check:
# that one omits the currently-RUNNING subtraction
# because it documents "assumed no RUNNING steps at this point"
# (it runs after the builder has stopped),
# so the two have different semantics despite the surface resemblance.
RESOURCE_UNAVAILABLE = f"""
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
"""


# Select the single highest-priority PENDING step ready for dispatch:
# a hash-checkable step (no resource check needed) if one exists,
# otherwise a step ready to execute (subject to resource availability).
# Checkable steps always take priority over runnable ones --
# checking is cheap and unlocks more work early --
# regardless of relative _tail_time, hence `_has_hash DESC` leads the ORDER BY.
#
# This walks the step_dispatch partial index (defined in STEP_SCHEMA next to
# STEP_DISPATCH_WHERE) in priority order and stops at the first eligible row, instead of
# materializing and sorting the whole PENDING candidate set on every dispatch. That is only
# possible because step._has_hash and step._ready are materialized, trigger-maintained
# columns (see STEP_SCHEMA and Scheduler._update_meta_ready/RECOMPUTE_READY) -- the
# correlated UNAVAILABLE_INPUT/step_hash EXISTS checks this query used to run per candidate
# row are gone from here entirely.
#
# node.detached and the resource check are intentionally NOT part of step_dispatch's WHERE
# clause (a partial index can only reference columns of the indexed table), so they are
# re-checked lazily per examined index row instead -- cheap in practice since both rarely
# reject, so this stays effectively O(1) even though it is not, strictly speaking, index-only.
#
# `INDEXED BY` is required: SQLite's planner does not pick step_dispatch voluntarily, even
# after ANALYZE (measured separately). It pins the plan and fails loudly (query error) if
# the index is ever missing -- acceptable since the index is part of the schema executed on
# every database open. Do not add node.label or node.i to the ORDER BY: any term outside
# the index forces a temp B-tree sort of all matching rows, defeating this query's purpose.
# The tie-break is therefore the index's implicit `step.node ASC` primary-key suffix
# (deterministic, but a different order than the dropped `label ASC`).
#
# step._implied_need > ? (Workflow.need_threshold, bound by _get_next_step()) sits outside
# STEP_DISPATCH_WHERE, which must stay static and textually match step_dispatch's WHERE
# clause -- a per-build runtime value cannot appear there. Without targets the threshold is
# OPTIONAL, which this term already implies via STEP_DISPATCH_WHERE, so behavior is
# unchanged; with targets it is DEFAULT, making DEFAULT-need steps optional-in-effect.
SELECT_NEXT_STEP = f"""
SELECT node.i, node.label, step._has_hash
FROM step INDEXED BY step_dispatch
JOIN node ON node.i = step.node
WHERE
    {STEP_DISPATCH_WHERE} AND
    step._implied_need > ? AND
    NOT node.detached AND
    (step._has_hash OR NOT EXISTS ({RESOURCE_UNAVAILABLE}))
ORDER BY
    step._has_hash DESC,
    (step._implied_need = {Need.PLAN.value}) DESC,
    step._tail_time / (1 + step.postpone_count) DESC
LIMIT 1
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
#
# step._implied_need > ? binds Workflow.need_threshold, the same property SELECT_NEXT_STEP
# binds, so the dispatch and reporting thresholds can never diverge. Without targets this is
# OPTIONAL, equivalent to the old static `!= OPTIONAL` filter since OPTIONAL is Need's
# minimum value; with targets it is DEFAULT, so DEFAULT-implied PENDING steps (never
# selected for dispatch) are no longer reported.
SELECT_PENDING_REASONS = f"""
SELECT
    node.i,
    node.label,
    step._safe,
    step.postponed AS postponed,
    EXISTS ({UNAVAILABLE_INPUT}) AS has_unavailable_inputs,
    EXISTS (
        SELECT 1 FROM step_resource AS req
        LEFT JOIN available_resource AS avail ON avail.name = req.name
        WHERE req.node = node.i AND (avail.name IS NULL OR avail.units < req.units)
    ) AS has_resource_issue
FROM node
JOIN step ON node.i = step.node
WHERE step.state = {StepState.PENDING.value} AND
    step._implied_need > ? AND
    NOT node.detached
{_ORDER_BY_PRIORITY}
"""


@attrs.define
class Scheduler:
    """Turn PENDING jobs into RUNNING jobs as the builder requests them."""

    workflow: Workflow = attrs.field()
    """The workflow that the scheduler is responsible for."""

    db: DBSession = attrs.field(kw_only=True)
    """The workflow database session, i.e. the same object as `workflow.db`.

    It is used directly as an async context manager,
    which acquires exclusive access to the database for the duration of a transaction.
    """

    use_duration: bool = attrs.field(kw_only=True, default=False)
    """Whether to use the duration of steps to optimize the execution order."""

    on_hold: bool = attrs.field(init=False, default=False)
    """Temporarily pause scheduling of jobs, e.g. interrupted by the user."""

    start_times: dict[int, int] = attrs.field(init=False, factory=dict)
    """Step node id -> `time.monotonic_ns()` at the moment it was dispatched to RUNNING.

    In-memory only (not persisted): used by `ran_concurrently()` to compare
    against a producer's `stop_times` entry.
    """

    stop_times: dict[int, int] = attrs.field(init=False, factory=dict)
    """Step node id -> `time.monotonic_ns()` at the moment it reached SUCCEEDED.

    In-memory only, pruned by `record_stop_time()`. See `start_times`.
    """

    new_durations: dict[int, float] = attrs.field(init=False, factory=dict)
    """Step node id -> most recently measured job duration, not yet written to the database.

    In-memory only. Populated by `job_completed()`, written and cleared by
    `flush_durations()` at the end of a build phase (see `Builder.finalize`/`Builder.stop`).
    """

    jobs: dict[int, Step] = attrs.field(init=False, factory=dict)
    """`Job.job_i` -> `Step`, for every job that has been created but not yet completed.

    Populated by `_derive_job()`, pruned by `job_completed()`. Used by `get_step()` to
    resolve RPC calls made by a step's child process back to the `Step` that made them.
    """

    job_counter: int = attrs.field(init=False, default=0)
    """Counter used to assign a unique `job_i` to each `Job` created by `_derive_job()`."""

    write_joblog: bool = attrs.field(kw_only=True, default=False)
    """Whether to record `--joblog` events."""

    #
    # Initialization
    #

    async def initialize(self, resources: str | None):
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
            # check_after, changed_after, and safe_update are hot-path temp tables used by
            # _update_meta_after()/_update_meta_safe(). Creating them once here, instead of on
            # every call, avoids repeated schema-cookie bumps (which invalidate SQLite's
            # prepared-statement cache) on the dispatch hot path.
            self.workflow.db.execute(INIT_CHECK_AFTER)
            self.workflow.db.execute(INIT_CHANGED_AFTER)
            self.workflow.db.execute(INIT_SAFE_UPDATE)
            # target_path backs UPDATE_CHECK_AFTER's target-elevation check.
            # Populated once here since Workflow.targets is
            # immutable for the lifetime of the director process.
            self.workflow.db.execute(
                "CREATE TEMPORARY TABLE IF NOT EXISTS target_path (path TEXT PRIMARY KEY)"
            )
            self.workflow.db.execute("DELETE FROM target_path")
            self.workflow.db.executemany(
                "INSERT INTO target_path VALUES (?)",
                ((str(path),) for path in sorted(self.workflow.targets)),
            )
            # target_dir backs UPDATE_CHECK_AFTER's directory-target elevation check.
            # Populated once here since Workflow.target_dirs is
            # immutable for the lifetime of the director process.
            self.workflow.db.execute(
                "CREATE TEMPORARY TABLE IF NOT EXISTS target_dir "
                "(path TEXT PRIMARY KEY, upper TEXT NOT NULL)"
            )
            self.workflow.db.execute("DELETE FROM target_dir")
            self.workflow.db.executemany(
                "INSERT INTO target_dir VALUES (?, ?)",
                (
                    (str(path), dir_range_upper(str(path)))
                    for path in sorted(self.workflow.target_dirs)
                ),
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
            self._update_meta_ready()

            # B) Identify the highest priority PENDING step that is ready for dispatch:
            #    a checkable step (stored hash, no resource check needed) if one exists,
            #    otherwise a runnable step (subject to resources).
            result = self._get_next_step()
            if result is None:
                logger.debug("No runnable steps found")
                return None
            step, has_hash = result
            job = self._derive_job(step)
            if has_hash:
                logger.debug("Derived checkable job: %s", job)
                logger.info("Pop %s", job.name)
                step.set_state(StepState.CHECKING)
            else:
                logger.debug("Queueing step %s", step)
                logger.debug("Derived job: %s", job)
                logger.info("Pop %s", job.name)
                step.set_state(StepState.RUNNING)
                self.start_times[step.i] = time.monotonic_ns()
            return job

    def record_stop_time(self, step_i: int, *, succeeded: bool) -> None:
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
            # Record stop times when there can be BUILT outputs to be used by other steps.
            # Only these may be approved by a post-hoc amend(inp=...) call in another step.
            self.stop_times[step_i] = time.monotonic_ns()
        if len(self.start_times) == 0:
            # If there are no more start_times, clear the dict to avoid holding old entries forever.
            self.stop_times.clear()
        else:
            # Clean up stop_times older than the oldest start_time,
            # since these will never be relevant for ran_concurrently() anymore.
            oldest_start = min(self.start_times.values())
            for other_step_i, stop_time in list(self.stop_times.items()):
                if stop_time < oldest_start:
                    del self.stop_times[other_step_i]

    def ran_concurrently(self, producer_i: int, consumer_i: int) -> bool:
        """Whether a consumer step started running before the producer step stopped.

        Parameters
        ----------
        producer_i
            Node id of the step that (re)built the file being amended as an input.
        consumer_i
            Node id of the step amending that file as an input.

        Returns
        -------
        overlapped
            `True` when the producer's `stop_times` entry and the consumer's
            `start_times` entry both exist and `start_time <= stop_time`, i.e. the
            consumer's execution window overlapped the producer's (a tie counts as
            overlapping: the conservative choice). `False` when either timestamp is
            missing.
        """
        stop_time = self.stop_times.get(producer_i)
        start_time = self.start_times.get(consumer_i)
        return stop_time is not None and start_time is not None and start_time <= stop_time

    def _update_meta_safe(self):
        """Update the "safe" metadata fields where needed."""
        db = self.workflow.db
        if not db.execute("SELECT EXISTS(SELECT 1 FROM step WHERE _check_safe)").fetchone()[0]:
            return
        # safe_update is created once in Scheduler.initialize().
        db.execute(EMPTY_SAFE_UPDATE)
        db.execute(SELECT_SAFE_UPDATE)
        cur = db.execute(APPLY_SAFE_UPDATE)
        logger.debug(f"Updated {cur.rowcount} _safe metadata field(s) for steps")
        cur = db.execute("UPDATE step SET _check_safe = 0 WHERE _check_safe")
        logger.debug(f"Updated {cur.rowcount} _check_safe metadata field(s) for steps")

    def _update_meta_after(self):
        """Update the "after" metadata fields where needed.

        Every flagged step is recomputed in the first iteration.
        Skipping the ones that also have a flagged (indirect) sink,
        on the grounds that propagation from that sink will reach them anyway,
        is not sound: propagation stops at the first step whose values do not change,
        so a flagged step two or more hops upstream can be missed entirely.
        It would then keep a stale `_implied_need` for good,
        because `_check_after` is cleared for all steps at the end of this method.
        """
        db = self.workflow.db
        if not db.execute("SELECT EXISTS(SELECT 1 FROM step WHERE _check_after)").fetchone()[0]:
            return
        # Not using executescript to preserve atomicity of the transaction.
        # check_after and changed_after are created once in Scheduler.initialize().
        db.execute(EMPTY_CHECK_AFTER)
        db.execute(PRUNE_DETACHED_CHECK_AFTER)
        ncheck = db.execute("SELECT COUNT(*) FROM check_after").fetchone()[0]
        first = True
        while ncheck > 0:
            logger.debug(f"Found {ncheck} sources to update (first={first})")
            # The first iteration is different: irrespective of having changed metadata fields of
            # the initial _check_after steps, we need to propagate at least once.
            cur = db.execute(UPDATE_CHECK_AFTER, {"first": first})
            changed_ids = cur.fetchall()
            db.execute(EMPTY_CHECK_AFTER)
            db.execute(EMPTY_CHANGED_AFTER)
            db.executemany("INSERT INTO changed_after(i) VALUES (?)", changed_ids)
            cur = db.execute(PROPAGATE_UPDATE_CHECK_AFTER)
            ncheck = cur.rowcount
            first = False
        logger.debug("Finished updating 'after' metadata fields")
        cur = db.execute("UPDATE step SET _check_after = 0 WHERE _check_after")
        logger.debug(f"Updated {cur.rowcount} _check_after metadata field(s) for steps")

    def _update_meta_ready(self):
        """Update the `_ready` metadata field where needed."""
        db = self.workflow.db
        if not db.execute("SELECT EXISTS(SELECT 1 FROM step WHERE _check_ready)").fetchone()[0]:
            return
        cur = db.execute(RECOMPUTE_READY)
        logger.debug(f"Updated {cur.rowcount} _ready metadata field(s) for steps")

    def _get_next_step(self) -> tuple[Step, bool] | None:
        """Fetch the single best PENDING step to dispatch, if any.

        Returns
        -------
        step_and_has_hash
            The step and whether it was selected via the hash-checkable path
            (`True`, transitions to `CHECKING`)
            or the runnable path (`False`, transitions to `RUNNING`).
            `None` if no PENDING step is currently eligible.
        """
        row = self.workflow.db.execute(
            SELECT_NEXT_STEP, (self.workflow.need_threshold.value,)
        ).fetchone()
        if row is None:
            return None
        i, label, has_hash = row
        return Step(self.workflow, i, label), bool(has_hash)

    def _next_job_i(self) -> int:
        """Return a fresh, unique id for a new `Job`."""
        self.job_counter += 1
        return self.job_counter

    def get_step(self, job_i: int) -> Step:
        """Resolve an RPC call's `job_i` argument to the `Step` it belongs to."""
        step = self.jobs.get(job_i)
        if step is None:
            raise ValueError(f"No job found for job_i={job_i}.")
        return step

    def _derive_job(self, step: Step) -> RunJob | ValidateAmendedJob:
        """Derive a Job instance for a step that is ready to be queued."""
        amended_inputs_ready = True
        inp_hashes = {}
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
                inp_hashes[path] = FileHash.from_json(hash_value)
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

        job_i = self._next_job_i()
        self.jobs[job_i] = step
        if amended_inputs_ready or step_hash is None:
            # All (amended) inputs are ready, or the job is not skippable.
            # When there is a hash, this will check if any inputs have changed since the last run,
            # and skip the job if not.
            # In all other cases, the job will be executed without skipping,
            # and the step hash will be updated after completion.
            job = RunJob(step, inp_hashes, env_deps, step_hash, job_i=job_i)
        else:
            # If the initial inputs are ready, but the amended inputs are not,
            # and there is a step hash, we need to validate the amended inputs first.
            # If they are not available, and if the existing inputs have changed,
            # they may also no longer be needed.
            job = ValidateAmendedJob(step, inp_hashes, env_deps, step_hash, job_i=job_i)
        if self.write_joblog:
            write_joblog_record("CREATED", job_i, job.name)
        return job

    async def job_completed(self, job):
        """Handle a completed job: drop its id -> step mapping and record its duration."""
        del self.jobs[job.job_i]
        if self.use_duration:
            self.new_durations[job.step.i] = job.duration()
        if self.write_joblog:
            write_joblog_record("COMPLETED", job.job_i, job.name)
        logger.info("Done %s", job.name)

    def build_completed(self):
        """Perform some finalization after the build has completed.

        - Reset the job counter.
        - Write accumulated step durations to the database and clear the buffer.
        - Clear the start/stop time buffers used to detect unfresh inputs.
        """
        self.job_counter = 0
        if len(self.new_durations) > 0:
            self.db.executemany(
                "UPDATE step SET duration = :duration WHERE node = :node "
                "AND ABS(duration - :duration) > 0.1 * duration",
                [
                    {"node": node, "duration": duration}
                    for node, duration in self.new_durations.items()
                ],
            )
            self.new_durations.clear()
        # Also clear the timings used to detect unfresh inputs (see ran_concurrently).
        # This is safe to do here because the builder is guaranteed to have no RUNNING steps.
        self.start_times.clear()
        self.stop_times.clear()

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
        cur = self.workflow.db.execute(
            SELECT_PENDING_REASONS, (self.workflow.need_threshold.value,)
        )
        for i, label, safe, postponed, unavailable_inputs, resource_issue in cur:
            step = Step(self.workflow, i, label)
            if not safe:
                reason = "unsafe"
            elif postponed or unavailable_inputs:
                reason = "inputs"
            elif resource_issue:
                reason = "resources"
            else:
                reason = "runnable"
            results.append((step, reason))
        return results
