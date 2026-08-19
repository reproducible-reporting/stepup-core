# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Root-cause analysis of `PENDING` steps left over at the end of a build phase.

Printing one page per pending step is `O(pending steps)`, which in a realistic workflow
means thousands of near-identical pages: one missing input file can leave hundreds of
steps pending, all for the same underlying reason. `analyze_pending` instead identifies
the small set of **root causes** (unavailable input files, unsatisfiable resources,
failed producers, ...) and, for each one, how many pending steps it transitively blocks.
The result (`PendingSummary`) is fixed-size, independent of how many steps are pending.

The step-level predicate for "this input blocks that step" (`UNAVAILABLE_INPUT_WHERE` in
`step.py`) is shared verbatim with the scheduler's dispatch query, so this analysis can
never disagree with dispatch about what "blocked" means.
"""

from collections.abc import Sequence

import attrs

from .enums import FileState, StepState
from .sqlite3 import DBSession
from .step import UNAVAILABLE_INPUT_WHERE
from .workflow import Workflow

__all__ = (
    "PendingInput",
    "PendingOther",
    "PendingResource",
    "PendingSummary",
    "analyze_pending",
)


MAX_ROWS = 10
"""Number of rows displayed per ranked table (`PendingSummary.inputs` / `.resources`)."""

RANK_POOL = 20
"""Number of roots kept (ranked by attributed count) before the exact-count pass.

Wider than `MAX_ROWS` so that a root whose attributed count under-represents it (because
ties were broken in some other root's favour, see the `Counting` note on `analyze_pending`)
still has a chance to surface once its *exact* count is known.
"""

# Root kinds, ordered by priority: lower wins when a step has candidate blockers of more
# than one kind (see the ORDER BY in _INSERT_PEND_BLOCKER below). Root kinds always win
# over a step->step edge (BLOCK_STEP), so a step directly blocked by (say) a dead-end file
# is attributed to that file rather than to whichever sibling pending step sorts first.
ROOT_FILE, ROOT_RESOURCE, ROOT_FAILED = 0, 1, 2
ROOT_DEFERRED, ROOT_OTHER, ROOT_RUNNABLE = 3, 4, 5
BLOCK_STEP = 6


@attrs.define
class PendingInput:
    """One row of the `Unavailable inputs` table: a dead-end input file."""

    state: FileState
    """The file's current state, e.g. `MISSING` or `UNDECLARED`."""

    path: str
    """The file's path."""

    detached: bool
    """Whether the file is detached, i.e. no step in the workflow declares it anymore."""

    nblocked: int
    """Exact number of pending steps this file transitively blocks."""


@attrs.define
class PendingResource:
    """One row of the `Blocked resources` table: an unsatisfiable named resource."""

    name: str
    """The resource name."""

    units_needed: int
    """Largest number of units required by any single step blocked on this resource."""

    units_available: int | None
    """Units available, or `None` when the resource is not defined at all."""

    nblocked: int
    """Exact number of pending steps this resource transitively blocks."""


@attrs.define
class PendingOther:
    """One count-only bucket on the `Other reasons` page."""

    nblocked: int
    """Number of pending steps attributed to this bucket. Zero means "omit the line"."""

    example: str | None
    """Label of the lowest-sorting step in the bucket, or `None` when `nblocked == 0`."""


@attrs.define
class PendingSummary:
    """Fixed-size explanation of why steps remained pending, produced by `analyze_pending`.

    The `inputs`/`resources` tables and the `Other reasons` buckets use different counting
    semantics on purpose: table counts are **exact** transitive counts (what a user acts
    on) and can overlap (a step blocked by two dead-end files is counted under both), while
    the bucket counts are **attributed** (each pending step counted under exactly one
    bucket), so `sum(bucket counts) == ntotal` regardless of overlap.
    """

    ntotal: int
    """Total number of pending steps in the reporting universe."""

    inputs: list[PendingInput]
    """Up to `MAX_ROWS` dead-end input files, ranked by exact blocked count, desc then path asc."""

    ninputs_hidden: int
    """Number of dead-end input files beyond `inputs`."""

    ninputs_hidden_blocked: int
    """Attributed (lower-bound) step count for the input files not shown in `inputs`."""

    resources: list[PendingResource]
    """Up to `MAX_ROWS` unsatisfiable resources, ranked by exact blocked count."""

    nresources_hidden: int
    """Number of unsatisfiable resources beyond `resources`."""

    nresources_hidden_blocked: int
    """Attributed (lower-bound) step count for the resources not shown in `resources`."""

    failed: PendingOther
    """Steps ultimately blocked by a `FAILED` step (a producer, or a creator ancestor)."""

    cyclic: PendingOther
    """Steps stuck in (or downstream of) a dynamic cycle: no root blocker reaches them."""

    deferred: PendingOther
    """Steps deferred on an unavailable dynamic input, but with no blocking input found
    at report time (a stale `deferred` flag; see `Step.has_unavailable_dynamic_input`).
    """

    other: PendingOther
    """Steps that are unsafe for a reason not covered above:
    their nearest chain-broken creator ancestor is neither pending-and-eligible nor
    `FAILED`, e.g. a `SUCCEEDED`-but-unsafe creator, or a creator below `need_threshold`.
    """

    runnable: PendingOther
    """Steps with no candidate blocker at all: dispatch should have picked them up.
    Not expected in practice: a non-empty bucket points at `_safe` metadata that is stale
    at report time, i.e. a gap in the `_check_safe` bookkeeping.
    """


#
# Scratch tables
#
# All temp tables use a `pend_` prefix, are DROPped before CREATE (so a previous call that
# raised mid-analysis cannot leave stale tables behind for the next one) and again after
# use. Everything happens inside the caller's transaction (see `analyze_pending`'s
# docstring): no `async with db:` here, matching `Scheduler`'s "information gathering"
# methods.
#

_PEND_TABLE_NAMES = (
    "pend_step",
    "pend_file_block",
    "pend_dead_file",
    "pend_unsafe_anc",
    "pend_resource",
    "pend_step_block",
    "pend_seed",
    "pend_blocker",
    "pend_attributed",
)


_CREATE_PEND_TABLES = (
    # The pending universe U: PENDING, above the need threshold, not detached.
    """
    CREATE TEMP TABLE pend_step (
        i INTEGER PRIMARY KEY,
        label TEXT NOT NULL,
        unsafe INTEGER NOT NULL,
        deferred INTEGER NOT NULL
    ) WITHOUT ROWID
    """,
    # Blocking (file -> step) edges: dep.source is an input file that blocks dep.sink from
    # running, i.e. UNAVAILABLE_INPUT_WHERE holds, or the deferred-with-unavailable-
    # dynamic-input test does. One row per (file, step) pair; a file can block many steps
    # and a step can have many blocking files.
    """
    CREATE TEMP TABLE pend_file_block (
        src_file INTEGER NOT NULL,
        dst_step INTEGER NOT NULL
    )
    """,
    "CREATE INDEX pend_file_block_src ON pend_file_block(src_file)",
    # Blocking files with no live producer: no step in U produces them, and no FAILED step
    # produces them either, so nothing left in the build will ever create them.
    """
    CREATE TEMP TABLE pend_dead_file (
        i INTEGER PRIMARY KEY,
        label TEXT NOT NULL,
        state INTEGER NOT NULL,
        detached INTEGER NOT NULL
    ) WITHOUT ROWID
    """,
    # For every unsafe step in U, its nearest chain-broken creator ancestor (the first
    # ancestor whose own state/holding is what actually makes the step unsafe). At most one
    # row per dst_step: see the comment on _INSERT_PEND_UNSAFE_ANC.
    """
    CREATE TEMP TABLE pend_unsafe_anc (
        dst_step INTEGER PRIMARY KEY,
        anc INTEGER NOT NULL
    ) WITHOUT ROWID
    """,
    # Resources required by a step in U that are undefined or over-committed. `id` is a
    # plain (non-WITHOUT-ROWID) autoincrementing surrogate key, used as `pend_seed`/
    # `pend_blocker`'s `root_id` for this root kind.
    """
    CREATE TEMP TABLE pend_resource (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        units_needed INTEGER NOT NULL,
        units_available INTEGER
    )
    """,
    # The full step -> step blocking relation over U (every candidate edge, not just the
    # primary one), used by the exact-count closure. src_step blocks dst_step.
    """
    CREATE TEMP TABLE pend_step_block (
        src_step INTEGER NOT NULL,
        dst_step INTEGER NOT NULL
    )
    """,
    "CREATE INDEX pend_step_block_src ON pend_step_block(src_step)",
    # Direct root -> step edges, populated only for the two root kinds that ever need an
    # exact (non-attributed) transitive count: FILE and RESOURCE (see _build_inputs/
    # _build_resources). FAILED/DEFERRED/OTHER/RUNNABLE buckets only ever report
    # attributed counts (from pend_attributed), so they need no entry here.
    """
    CREATE TEMP TABLE pend_seed (
        root_kind INTEGER NOT NULL,
        root_id INTEGER NOT NULL,
        dst_step INTEGER NOT NULL
    )
    """,
    "CREATE INDEX pend_seed_root ON pend_seed(root_kind, root_id)",
    # The single primary blocker chosen for every step in U (root kinds win over
    # BLOCK_STEP; see the ROOT_* ordering above). Out-degree 1 makes the reverse relation a
    # forest, which pend_attributed walks in O(pending).
    """
    CREATE TEMP TABLE pend_blocker (
        dst_step INTEGER PRIMARY KEY,
        kind INTEGER NOT NULL,
        src INTEGER NOT NULL
    ) WITHOUT ROWID
    """,
    "CREATE INDEX pend_blocker_src ON pend_blocker(src)",
    # The attribution forest walk's result: every step in U mapped to the root (kind, id)
    # it is ultimately attributed to. A step absent here is in (or downstream of) a dynamic
    # cycle: see the `cyclic` bucket.
    """
    CREATE TEMP TABLE pend_attributed (
        dst_step INTEGER PRIMARY KEY,
        root_kind INTEGER NOT NULL,
        root_id INTEGER NOT NULL
    ) WITHOUT ROWID
    """,
    "CREATE INDEX pend_attributed_root ON pend_attributed(root_kind, root_id)",
)


def _drop_pend_tables(db: DBSession) -> None:
    """Drop every `pend_*` scratch table, if present."""
    for name in _PEND_TABLE_NAMES:
        db.execute(f"DROP TABLE IF EXISTS {name}")


_SELECT_NTOTAL = f"""
SELECT COUNT(*) FROM step JOIN node ON node.i = step.node
WHERE step.state = {StepState.PENDING.value}
  AND step._implied_need > ?
  AND NOT node.detached
"""


# The pending universe U, exactly as SELECT_NEXT_STEP's threshold binds it, so reporting
# can never diverge from dispatch. `unsafe` recomputes STEP_DISPATCH_WHERE's safety
# disjunct live (rather than trusting the materialized _ready) so a stale flag cannot
# silently produce a wrong bucket; `_safe` is read from the column, matching
# STEP_DISPATCH_WHERE (recomputing it would mean duplicating SELECT_SAFE_UPDATE).
_INSERT_PEND_STEP = f"""
INSERT INTO pend_step(i, label, unsafe, deferred)
SELECT node.i, node.label,
       NOT (step._safe OR (step._has_hash AND step._safe_ignoring_hold)),
       step.deferred
FROM node JOIN step ON node.i = step.node
WHERE step.state = {StepState.PENDING.value}
  AND step._implied_need > ?
  AND NOT node.detached
"""


# Blocking inputs of every step in U: UNAVAILABLE_INPUT_WHERE's ordinary dispatch test, or
# (only for a deferred step) the has_unavailable_dynamic_input test that set `deferred`
# in the first place -- a deferred step's dynamic inputs are not otherwise covered by
# UNAVAILABLE_INPUT_WHERE once they are no longer attached and PLANNED/OUTDATED
# (e.g. UNDECLARED, which implies detached, or MISSING),
# which is exactly the gap defer_cap-shaped workflows fall into.
_INSERT_PEND_FILE_BLOCK = f"""
INSERT INTO pend_file_block(src_file, dst_step)
SELECT DISTINCT dep.source, pend_step.i
FROM pend_step
JOIN dependency AS dep ON dep.sink = pend_step.i
JOIN file AS input_file ON input_file.node = dep.source
JOIN node AS input_node ON input_node.i = dep.source
LEFT JOIN dynamic_dep ON dynamic_dep.i = dep.i
WHERE ({UNAVAILABLE_INPUT_WHERE})
   OR (
       pend_step.deferred AND dynamic_dep.i IS NOT NULL
       AND input_file.state NOT IN ({FileState.CONFIRMED.value}, {FileState.BUILT.value})
   )
"""


# Blocking files with no live producer: no dependency-edge producer (see the module
# docstring: `node.creator` is provenance, not this relation -- a declared static/missing
# file has no producer here at all) that is either in U or FAILED.
_INSERT_PEND_DEAD_FILE = f"""
INSERT INTO pend_dead_file(i, label, state, detached)
SELECT DISTINCT f.node, node.label, f.state, node.detached
FROM pend_file_block AS pfb
JOIN file AS f ON f.node = pfb.src_file
JOIN node ON node.i = pfb.src_file
WHERE NOT EXISTS (
    SELECT 1 FROM dependency AS pdep
    JOIN node AS pnode ON pnode.i = pdep.source
    WHERE pdep.sink = pfb.src_file AND pnode.kind = 'step'
      AND (
          pnode.i IN (SELECT i FROM pend_step)
          OR EXISTS (
              SELECT 1 FROM step WHERE step.node = pnode.i AND step.state = {StepState.FAILED.value}
          )
      )
)
"""


# The nearest chain-broken creator ancestor of every unsafe step in U: walk up the creator
# chain through ancestors that are RUNNING/SUCCEEDED and not holding (i.e. not
# chain-broken), and stop at the first one that is. Terminates on "no step row" (the root
# is its own creator and has none), which is safe here: a step only seeds this walk because
# it is unsafe, which (by construction of _safe) guarantees a real, non-root ancestor along
# its chain is chain-broken. _holding > 0 is unreachable at report time (no step is RUNNING
# once the builder has stopped) but is included for correctness, matching _safe's own
# definition.
_INSERT_PEND_UNSAFE_ANC = f"""
INSERT INTO pend_unsafe_anc(dst_step, anc)
WITH RECURSIVE up(dst_step, anc) AS (
    SELECT pend_step.i, node.creator
    FROM pend_step JOIN node ON node.i = pend_step.i
    WHERE pend_step.unsafe

    UNION ALL

    SELECT up.dst_step, node.creator
    FROM up
    JOIN node ON node.i = up.anc
    JOIN step ON step.node = up.anc
    WHERE step.state IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value})
      AND step._holding = 0
)
SELECT up.dst_step, up.anc
FROM up JOIN step ON step.node = up.anc
WHERE step.state NOT IN ({StepState.RUNNING.value}, {StepState.SUCCEEDED.value})
   OR step._holding > 0
"""


# Resources required by a step in U that are undefined or over-committed. No RUNNING
# subtraction (unlike scheduler.RESOURCE_UNAVAILABLE): this runs after the builder has
# stopped, so there is nothing running to subtract. units_needed is the largest requirement
# among the steps actually blocked on this resource, i.e. what a user would need to raise
# the limit to in order to unblock at least one of them.
_INSERT_PEND_RESOURCE = """
INSERT INTO pend_resource(name, units_needed, units_available)
SELECT req.name, MAX(req.units), MAX(avail.units)
FROM pend_step
JOIN step_resource AS req ON req.node = pend_step.i
LEFT JOIN available_resource AS avail ON avail.name = req.name
WHERE avail.name IS NULL OR avail.units < req.units
GROUP BY req.name
"""


# The full step -> step blocking relation over U: src_step blocks dst_step because src_step
# produces a blocking input of dst_step, or because src_step is dst_step's nearest
# chain-broken creator ancestor and that ancestor is itself in U (still pending and
# eligible, i.e. dst_step is waiting for it rather than for something outside U).
_INSERT_PEND_STEP_BLOCK = """
INSERT INTO pend_step_block(src_step, dst_step)
SELECT prod.i, pfb.dst_step
FROM pend_file_block AS pfb
JOIN dependency AS pdep ON pdep.sink = pfb.src_file
JOIN node AS prod ON prod.i = pdep.source AND prod.kind = 'step'
WHERE prod.i IN (SELECT i FROM pend_step)

UNION

SELECT pua.anc, pua.dst_step
FROM pend_unsafe_anc AS pua
WHERE pua.anc IN (SELECT i FROM pend_step)
"""


_INSERT_PEND_SEED_FILE = f"""
INSERT INTO pend_seed(root_kind, root_id, dst_step)
SELECT DISTINCT {ROOT_FILE}, pdf.i, pfb.dst_step
FROM pend_file_block AS pfb
JOIN pend_dead_file AS pdf ON pdf.i = pfb.src_file
"""


_INSERT_PEND_SEED_RESOURCE = f"""
INSERT INTO pend_seed(root_kind, root_id, dst_step)
SELECT DISTINCT {ROOT_RESOURCE}, pr.id, req.node
FROM step_resource AS req
JOIN pend_resource AS pr ON pr.name = req.name
LEFT JOIN available_resource AS avail ON avail.name = req.name
WHERE req.node IN (SELECT i FROM pend_step)
  AND (avail.name IS NULL OR avail.units < req.units)
"""


# The single primary blocker per step in U: one UNION ALL of every candidate kind, keeping
# only the top-ranked candidate per dst_step (ROW_NUMBER, partitioned by dst_step, ordered
# by kind then a deterministic tie-break). Root kinds win over BLOCK_STEP by construction
# of the ROOT_*/BLOCK_STEP integer values (see their definitions above). `src_label` is the
# empty string for ROOT_DEFERRED/ROOT_OTHER, which have at most one candidate row per
# dst_step already (no tie to break); ROOT_RESOURCE uses the resource name so multiple
# unsatisfiable resources on the same step still sort deterministically.
_INSERT_PEND_BLOCKER = f"""
INSERT INTO pend_blocker(dst_step, kind, src)
SELECT dst_step, kind, src FROM (
    SELECT
        dst_step, kind, src,
        ROW_NUMBER() OVER (
            PARTITION BY dst_step ORDER BY kind, src_label, src
        ) AS rn
    FROM (
        -- FILE: blocked by a dead-end input file.
        SELECT pfb.dst_step AS dst_step, {ROOT_FILE} AS kind, pdf.i AS src, pdf.label AS src_label
        FROM pend_file_block AS pfb
        JOIN pend_dead_file AS pdf ON pdf.i = pfb.src_file

        UNION ALL

        -- RESOURCE: blocked by an unsatisfiable named resource.
        SELECT
            req.node AS dst_step, {ROOT_RESOURCE} AS kind, pr.id AS src, pr.name AS src_label
        FROM step_resource AS req
        JOIN pend_resource AS pr ON pr.name = req.name
        LEFT JOIN available_resource AS avail ON avail.name = req.name
        WHERE req.node IN (SELECT i FROM pend_step)
          AND (avail.name IS NULL OR avail.units < req.units)

        UNION ALL

        -- FAILED: blocked by an input whose producer FAILED.
        SELECT
            pfb.dst_step AS dst_step, {ROOT_FAILED} AS kind, prod.i AS src, prod.label AS src_label
        FROM pend_file_block AS pfb
        JOIN dependency AS pdep ON pdep.sink = pfb.src_file
        JOIN node AS prod ON prod.i = pdep.source AND prod.kind = 'step'
        JOIN step AS pstep ON pstep.node = prod.i
        WHERE pstep.state = {StepState.FAILED.value}

        UNION ALL

        -- FAILED: unsafe because the nearest chain-broken creator ancestor FAILED.
        SELECT
            pua.dst_step AS dst_step, {ROOT_FAILED} AS kind, pua.anc AS src, anc.label AS src_label
        FROM pend_unsafe_anc AS pua
        JOIN node AS anc ON anc.i = pua.anc
        JOIN step AS astep ON astep.node = pua.anc
        WHERE astep.state = {StepState.FAILED.value}

        UNION ALL

        -- DEFERRED: deferred, but no blocking input found (a stale deferred flag).
        SELECT
            pend_step.i AS dst_step, {ROOT_DEFERRED} AS kind, pend_step.i AS src, '' AS src_label
        FROM pend_step
        WHERE pend_step.deferred
          AND NOT EXISTS (
              SELECT 1 FROM pend_file_block WHERE pend_file_block.dst_step = pend_step.i
          )

        UNION ALL

        -- OTHER: unsafe, but the nearest chain-broken ancestor is outside U and not FAILED.
        SELECT pua.dst_step AS dst_step, {ROOT_OTHER} AS kind, pua.anc AS src, '' AS src_label
        FROM pend_unsafe_anc AS pua
        WHERE pua.anc NOT IN (SELECT i FROM pend_step)
          AND NOT EXISTS (
              SELECT 1 FROM step
              WHERE step.node = pua.anc AND step.state = {StepState.FAILED.value}
          )

        UNION ALL

        -- BLOCK_STEP: blocked by another step that is itself in U.
        SELECT
            psb.dst_step AS dst_step, {BLOCK_STEP} AS kind,
            psb.src_step AS src, src_node.label AS src_label
        FROM pend_step_block AS psb
        JOIN node AS src_node ON src_node.i = psb.src_step
    )
) WHERE rn = 1
"""


# RUNNABLE: whatever is left has no candidate blocker of any kind, i.e. dispatch should
# have picked it up already. Not expected in practice (see PendingSummary.runnable).
_INSERT_PEND_BLOCKER_RUNNABLE = f"""
INSERT INTO pend_blocker(dst_step, kind, src)
SELECT i, {ROOT_RUNNABLE}, i FROM pend_step
WHERE i NOT IN (SELECT dst_step FROM pend_blocker)
"""


# Attribute every step in U to exactly one root, by walking the primary-blocker forest
# (pend_blocker restricted to BLOCK_STEP has out-degree 1, see its comment) from every
# root-kind seed. UNION ALL is safe here (no row can repeat): a step cannot consume its own
# output (add_source raises CyclicError) or be its own creator (the node table's own CHECK
# constraint), so self-parenting is impossible, and every non-root-seeded step has exactly
# one parent in this forest. A step inside (or downstream of) a dynamic cycle has a parent
# chain that never leaves the cycle, so it is never reached by any seed and is absent from
# the result -- this is exactly the residual the `cyclic` bucket reports, with no
# cycle-detection code needed.
_INSERT_PEND_ATTRIBUTED = f"""
INSERT INTO pend_attributed(dst_step, root_kind, root_id)
WITH RECURSIVE walk(i, root_kind, root_id) AS (
    SELECT dst_step, kind, src FROM pend_blocker WHERE kind != {BLOCK_STEP}

    UNION ALL

    SELECT pend_blocker.dst_step, walk.root_kind, walk.root_id
    FROM walk
    JOIN pend_blocker ON pend_blocker.kind = {BLOCK_STEP} AND pend_blocker.src = walk.i
)
SELECT i, root_kind, root_id FROM walk
"""


def analyze_pending(workflow: Workflow) -> PendingSummary:
    """Explain why steps remained pending, as a fixed-size root-cause summary.

    Must be called after the builder has stopped (no `RUNNING` or `CHECKING` steps) and
    with the database lock held by the caller (`async with db:`), following the
    "Information gathering" convention in `scheduler.py`.
    It reads the `available_resource` temp table, which `Scheduler.initialize()` owns.

    Parameters
    ----------
    workflow
        The workflow to analyze.

    Returns
    -------
    summary
        The root-cause summary. `PendingSummary(ntotal=0, ...)` when no step remained
        pending, computed with a single `COUNT(*)` so the common (successful-build) path
        stays cheap.
    """
    summary, _attributed_totals = _analyze_pending(workflow)
    return summary


def _analyze_pending(workflow: Workflow) -> tuple[PendingSummary, dict[int, int]]:
    """Core of `analyze_pending`.

    Additionally returns `{root_kind: attributed_count}` (for every kind present in
    `pend_attributed`, including `ROOT_FILE`/`ROOT_RESOURCE`, which `PendingSummary` only
    ever reports as *exact*, overlapping counts). `analyze_pending` discards this second
    value; `tests/test_pending.py` uses it to assert the partition invariant
    (`sum(totals.values()) + cyclic.nblocked == ntotal`), which `PendingSummary` alone
    cannot express.
    """
    db = workflow.db
    threshold = workflow.need_threshold.value
    ntotal = db.execute(_SELECT_NTOTAL, (threshold,)).fetchone()[0]
    if ntotal == 0:
        empty = PendingOther(nblocked=0, example=None)
        summary = PendingSummary(
            ntotal=0,
            inputs=[],
            ninputs_hidden=0,
            ninputs_hidden_blocked=0,
            resources=[],
            nresources_hidden=0,
            nresources_hidden_blocked=0,
            failed=empty,
            cyclic=empty,
            deferred=empty,
            other=empty,
            runnable=empty,
        )
        return summary, {}

    _drop_pend_tables(db)
    try:
        for stmt in _CREATE_PEND_TABLES:
            db.execute(stmt)
        db.execute(_INSERT_PEND_STEP, (threshold,))
        db.execute(_INSERT_PEND_FILE_BLOCK)
        db.execute(_INSERT_PEND_DEAD_FILE)
        db.execute(_INSERT_PEND_UNSAFE_ANC)
        db.execute(_INSERT_PEND_RESOURCE)
        db.execute(_INSERT_PEND_STEP_BLOCK)
        db.execute(_INSERT_PEND_SEED_FILE)
        db.execute(_INSERT_PEND_SEED_RESOURCE)
        db.execute(_INSERT_PEND_BLOCKER)
        db.execute(_INSERT_PEND_BLOCKER_RUNNABLE)
        db.execute(_INSERT_PEND_ATTRIBUTED)

        inputs, ninputs_hidden, ninputs_hidden_blocked = _build_inputs(db)
        resources, nresources_hidden, nresources_hidden_blocked = _build_resources(db)
        attributed_totals = dict(
            db.execute("SELECT root_kind, COUNT(*) FROM pend_attributed GROUP BY root_kind")
        )
        summary = PendingSummary(
            ntotal=ntotal,
            inputs=inputs,
            ninputs_hidden=ninputs_hidden,
            ninputs_hidden_blocked=ninputs_hidden_blocked,
            resources=resources,
            nresources_hidden=nresources_hidden,
            nresources_hidden_blocked=nresources_hidden_blocked,
            failed=_bucket(db, ROOT_FAILED),
            cyclic=_cyclic_bucket(db),
            deferred=_bucket(db, ROOT_DEFERRED),
            other=_bucket(db, ROOT_OTHER),
            runnable=_bucket(db, ROOT_RUNNABLE),
        )
        return summary, attributed_totals
    finally:
        _drop_pend_tables(db)


#
# Ranking and exact counts
#
# Two counting semantics, on purpose: ranking (which roots matter most) uses the
# attributed, O(pending) counts from pend_attributed; the counts actually displayed for the
# top MAX_ROWS are exact, from a dedicated reachability closure over the full
# pend_step_block relation. Attribution alone misranks whenever roots overlap (a shared
# root can look smaller than it truly is, because ties in the primary-blocker choice always
# resolve the same way), so ranking keeps a wider pool (RANK_POOL) before the exact counts
# settle which roots are actually displayed.
#


def _rank_pool(db: DBSession, root_kind: int) -> list[tuple[int, int]]:
    """Return `(root_id, attributed_count)` for every root of `root_kind`, ranked desc."""
    return db.execute(
        "SELECT root_id, COUNT(*) AS n FROM pend_attributed "
        "WHERE root_kind = ? GROUP BY root_id ORDER BY n DESC, root_id ASC",
        (root_kind,),
    ).fetchall()


def _exact_counts(db: DBSession, root_kind: int, root_ids: Sequence[int]) -> dict[int, int]:
    """Return the exact transitive step count for each of `root_ids`.

    Computed by one recursive closure over `pend_step_block`, seeded from `pend_seed`.
    `UNION` (not `UNION ALL`) is required: the real blocking relation has diamonds (a step
    blocked by two roots) and, transitively, cycles, so a naive `UNION ALL` walk would not
    terminate or would over-count.
    """
    if len(root_ids) == 0:
        return {}
    placeholders = ", ".join("?" * len(root_ids))
    rows = db.execute(
        f"""
        WITH RECURSIVE reach(root_id, i) AS (
            SELECT root_id, dst_step FROM pend_seed
            WHERE root_kind = ? AND root_id IN ({placeholders})

            UNION

            SELECT reach.root_id, pend_step_block.dst_step
            FROM reach
            JOIN pend_step_block ON pend_step_block.src_step = reach.i
        )
        SELECT root_id, COUNT(*) FROM reach GROUP BY root_id
        """,
        (root_kind, *root_ids),
    ).fetchall()
    return dict(rows)


def _rank_display(db: DBSession, root_kind: int) -> tuple[list[int], dict[int, int], int, int]:
    """Select up to `MAX_ROWS` roots of `root_kind` to display, by exact count.

    Returns
    -------
    displayed
        Root ids to display (at most `MAX_ROWS`), in no particular order --
        callers sort them by their own presentation key (e.g. path/name) once they have
        fetched the corresponding metadata.
    exact
        Exact transitive step count for every id in `displayed`.
    nhidden
        Number of roots of this kind beyond `displayed`.
    nhidden_blocked
        Attributed step count summed over every root beyond `displayed`
        (a lower bound: exact counts are not computed for hidden roots).
    """
    ranked = _rank_pool(db, root_kind)
    total_attributed = sum(n for _, n in ranked)
    pool_ids = [root_id for root_id, _ in ranked[:RANK_POOL]]
    exact = _exact_counts(db, root_kind, pool_ids)
    displayed = sorted(pool_ids, key=lambda root_id: (-exact[root_id], root_id))[:MAX_ROWS]
    nhidden = len(ranked) - len(displayed)
    displayed_attributed = sum(n for root_id, n in ranked if root_id in displayed)
    nhidden_blocked = total_attributed - displayed_attributed
    return displayed, exact, nhidden, nhidden_blocked


def _build_inputs(db: DBSession) -> tuple[list[PendingInput], int, int]:
    """Build `PendingSummary.inputs` and its hidden-row counters."""
    displayed, exact, nhidden, nhidden_blocked = _rank_display(db, ROOT_FILE)
    meta = {}
    if len(displayed) > 0:
        placeholders = ", ".join("?" * len(displayed))
        for i, label, state, detached in db.execute(
            f"SELECT i, label, state, detached FROM pend_dead_file WHERE i IN ({placeholders})",
            displayed,
        ):
            meta[i] = (label, FileState(state), bool(detached))
    inputs = sorted(
        (
            PendingInput(state=state, path=label, detached=detached, nblocked=exact[i])
            for i, (label, state, detached) in meta.items()
        ),
        key=lambda row: (-row.nblocked, row.path),
    )
    return inputs, nhidden, nhidden_blocked


def _build_resources(db: DBSession) -> tuple[list[PendingResource], int, int]:
    """Build `PendingSummary.resources` and its hidden-row counters."""
    displayed, exact, nhidden, nhidden_blocked = _rank_display(db, ROOT_RESOURCE)
    meta = {}
    if len(displayed) > 0:
        placeholders = ", ".join("?" * len(displayed))
        for i, name, units_needed, units_available in db.execute(
            "SELECT id, name, units_needed, units_available "
            f"FROM pend_resource WHERE id IN ({placeholders})",
            displayed,
        ):
            meta[i] = (name, units_needed, units_available)
    resources = sorted(
        (
            PendingResource(
                name=name,
                units_needed=units_needed,
                units_available=units_available,
                nblocked=exact[i],
            )
            for i, (name, units_needed, units_available) in meta.items()
        ),
        key=lambda row: (-row.nblocked, row.name),
    )
    return resources, nhidden, nhidden_blocked


#
# Other-reasons buckets
#


def _bucket(db: DBSession, root_kind: int) -> PendingOther:
    """Build one attributed-count bucket (`failed`/`deferred`/`other`/`runnable`)."""
    nblocked, example = db.execute(
        "SELECT COUNT(*), MIN(pend_step.label) FROM pend_attributed "
        "JOIN pend_step ON pend_step.i = pend_attributed.dst_step "
        "WHERE pend_attributed.root_kind = ?",
        (root_kind,),
    ).fetchone()
    return PendingOther(nblocked=nblocked, example=example)


def _cyclic_bucket(db: DBSession) -> PendingOther:
    """Build the `cyclic` bucket: steps absent from `pend_attributed` entirely."""
    nblocked, example = db.execute(
        "SELECT COUNT(*), MIN(label) FROM pend_step "
        "WHERE i NOT IN (SELECT dst_step FROM pend_attributed)"
    ).fetchone()
    return PendingOther(nblocked=nblocked, example=example)
