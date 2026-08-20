# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.pending."""

import time

from conftest import amend_step, fake_hash

from stepup.core.enums import FileState, HashUpdateCause, StepState
from stepup.core.file import File
from stepup.core.hash import FileHash
from stepup.core.pending import (
    MAX_ROWS,
    PendingOther,
    PendingSummary,
    _analyze_pending,
    analyze_pending,
)
from stepup.core.scheduler import Scheduler
from stepup.core.step import Step
from stepup.core.workflow import Workflow


async def _prepare(workflow: Workflow, resources: str | None = None) -> tuple[Scheduler, Step]:
    """Initialize a `Scheduler` for `workflow` and mark its `plan.py` step SUCCEEDED.

    This gives every step subsequently defined under `plan` a trivially safe ancestor
    chain, so a test only becomes unsafe/blocked for the reason it deliberately sets up.
    """
    scheduler = Scheduler(workflow, db=workflow.db)
    await scheduler.initialize(resources)
    async with workflow.db:
        plan = workflow.find(Step, "./plan.py")
        plan.set_state(StepState.SUCCEEDED)
    return scheduler, plan


async def _settle(workflow: Workflow, scheduler: Scheduler) -> None:
    """Recompute `_safe`/`_implied_need`/`_ready` without dispatching any step.

    Mirrors the metadata-update half of `Scheduler.pop_next_job()`, minus the actual
    dispatch, so a test's manually-constructed graph ends up with the same derived columns
    `analyze_pending` reads as a real build would leave behind.
    """
    async with workflow.db:
        scheduler._update_meta_safe()
        scheduler._update_meta_after()
        scheduler._update_meta_ready()


def _declare_static(workflow: Workflow, creator: Step, paths: list[str]) -> None:
    """Declare `paths` as static and confirm them present, i.e. `FileState.CONFIRMED`."""
    workflow.declare_static_files(creator, paths)
    workflow.update_file_hashes(
        {path: fake_hash(path) for path in paths}, cause=HashUpdateCause.CONFIRMED
    )


def _declare_missing(workflow: Workflow, creator: Step, paths: list[str]) -> None:
    """Declare `paths` as static and confirm them absent, i.e. `FileState.MISSING`."""
    workflow.declare_static_files(creator, paths)
    workflow.update_file_hashes(
        {path: FileHash.unknown() for path in paths}, cause=HashUpdateCause.CONFIRMED
    )


#
# Empty case
#


async def test_empty(wfp: Workflow):
    await _prepare(wfp)
    async with wfp.db:
        summary = analyze_pending(wfp)
    empty = PendingOther(nblocked=0, example=None)
    assert summary == PendingSummary(
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


#
# Missing static input, a dead end blocking a chain of steps
#


async def test_missing_input_blocks_chain(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    async with wfp.db:
        _declare_missing(wfp, plan, ["missing.txt"])
        wfp.define_step(plan, "step1", inp_paths=["missing.txt"], out_paths=["out1.txt"])
        wfp.define_step(plan, "step2", inp_paths=["out1.txt"], out_paths=["out2.txt"])
        wfp.define_step(plan, "step3", inp_paths=["out2.txt"], out_paths=["out3.txt"])
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == 3
    assert len(summary.inputs) == 1
    row = summary.inputs[0]
    assert row.path == "missing.txt"
    assert row.state == FileState.MISSING
    assert row.detached is False
    assert row.nblocked == 3
    assert summary.ninputs_hidden == 0
    assert summary.ninputs_hidden_blocked == 0
    assert summary.cyclic.nblocked == 0
    assert summary.failed.nblocked == 0
    assert summary.deferred.nblocked == 0
    assert summary.other.nblocked == 0
    assert summary.runnable.nblocked == 0


#
# Detached input
#


async def test_detached_input(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    async with wfp.db:
        wfp.define_step(plan, "producer", out_paths=["gen.txt"])
        producer = wfp.find(Step, "producer")
        producer.detach()
        wfp.define_step(plan, "consumer", inp_paths=["gen.txt"])
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == 1
    assert len(summary.inputs) == 1
    row = summary.inputs[0]
    assert row.path == "gen.txt"
    # Supplying the file to the consumer leaves it with the detached producer,
    # so it keeps the PLANNED state of an output that was never built.
    assert row.state == FileState.PLANNED
    assert row.detached is True
    assert row.nblocked == 1


#
# Diamond: exact counts differ from (and correct) attribution
#


async def test_diamond_exact_vs_attributed(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    n_a_only, n_b_only, n_both = 3, 5, 8
    async with wfp.db:
        # "config_a.txt" sorts before "data_b.txt", so the primary-blocker tie-break
        # always attributes a both-blocked step to config_a.txt.
        _declare_missing(wfp, plan, ["config_a.txt", "data_b.txt"])
        for k in range(n_a_only):
            wfp.define_step(plan, f"only_a_{k}", inp_paths=["config_a.txt"])
        for k in range(n_b_only):
            wfp.define_step(plan, f"only_b_{k}", inp_paths=["data_b.txt"])
        for k in range(n_both):
            wfp.define_step(plan, f"both_{k}", inp_paths=["config_a.txt", "data_b.txt"])
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == n_a_only + n_b_only + n_both
    by_path = {row.path: row for row in summary.inputs}
    assert by_path["config_a.txt"].nblocked == n_a_only + n_both
    # The point of this test: data_b.txt's exact count (n_b_only + n_both) is displayed,
    # not its (smaller) attributed count (n_b_only alone, since ties always resolve to
    # config_a.txt).
    assert by_path["data_b.txt"].nblocked == n_b_only + n_both


#
# Deferred with a detached dynamic input (the defer_cap regression)
#


async def test_deferred_detached_dynamic_input(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    async with wfp.db:
        wfp.define_step(plan, "work")
        work = wfp.find(Step, "work")
        work.set_state(StepState.RUNNING)
        amend_step(wfp, work, inp_paths=["never.txt"])
        never = wfp.find(File, "never.txt")
        assert never.get_state() == FileState.UNDECLARED
        never.detach()
        work.set_state(StepState.PENDING, deferred=True)
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == 1
    assert summary.runnable.nblocked == 0
    assert len(summary.inputs) == 1
    row = summary.inputs[0]
    assert row.path == "never.txt"
    assert row.state == FileState.UNDECLARED
    assert row.detached is True
    assert row.nblocked == 1


#
# Deferred with no blocking input (a stale deferred flag)
#


async def test_deferred_no_blocking_input(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    async with wfp.db:
        wfp.define_step(plan, "work")
        work = wfp.find(Step, "work")
        work.set_state(StepState.RUNNING)
        _declare_static(wfp, plan, ["side.txt"])
        amend_step(wfp, work, inp_paths=["side.txt"])
        work.set_state(StepState.PENDING, deferred=True)
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == 1
    assert summary.inputs == []
    assert summary.deferred.nblocked == 1
    assert summary.deferred.example == "work"
    assert summary.runnable.nblocked == 0


#
# Unsafe through a SUCCEEDED intermediate
#


async def test_unsafe_through_succeeded_intermediate(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    async with wfp.db:
        _declare_missing(wfp, plan, ["missing.txt"])
        wfp.define_step(plan, "ancestor", inp_paths=["missing.txt"])
        ancestor = wfp.find(Step, "ancestor")
        wfp.define_step(ancestor, "middle")
        middle = wfp.find(Step, "middle")
        middle.set_state(StepState.SUCCEEDED)
        wfp.define_step(middle, "descendant")
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    # ancestor and descendant; middle SUCCEEDED is outside the pending universe.
    assert summary.ntotal == 2
    assert len(summary.inputs) == 1
    assert summary.inputs[0].nblocked == 2
    assert summary.other.nblocked == 0
    assert summary.runnable.nblocked == 0


#
# Failed producer
#


async def test_failed_producer(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    async with wfp.db:
        wfp.define_step(plan, "producer", out_paths=["prod.txt"])
        producer = wfp.find(Step, "producer")
        producer.set_state(StepState.FAILED)
        wfp.define_step(plan, "consumer", inp_paths=["prod.txt"])
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == 1
    assert summary.inputs == []
    assert summary.failed.nblocked == 1
    assert summary.failed.example == "consumer"


#
# Dynamic cycle
#


async def test_dynamic_cycle(wfp: Workflow):
    """Reproduce `tests/examples/cyclic_dynamic`'s shape.

    A literal 2-step mutual-output cycle (`work1` depends on `work2`'s own output and vice
    versa) is rejected outright by `check_sources_acyclic` (`CyclicError`): the dependency
    graph itself must stay a DAG. The example instead has each step create a *product*
    step that produces the file the other depends on -- the creator edge to that product is
    provenance, not a `dependency` edge, so the cycle check (which only walks
    `dependency`) never sees it, while the resulting blocking relation between `work1` and
    `work2` (each waiting on a file produced by a pending descendant of the other) is
    exactly the four-step cycle `analyze_pending` must reduce to one residual bucket.
    """
    scheduler, plan = await _prepare(wfp)
    async with wfp.db:
        wfp.define_step(plan, "work1")
        wfp.define_step(plan, "work2")
        work1 = wfp.find(Step, "work1")
        work2 = wfp.find(Step, "work2")
        wfp.define_step(work1, "subs1", out_paths=["inp1.txt"])
        wfp.define_step(work2, "subs2", out_paths=["inp2.txt"])
        amend_step(wfp, work1, inp_paths=["inp2.txt"], out_paths=["out2.txt"])
        amend_step(wfp, work2, inp_paths=["inp1.txt"], out_paths=["out1.txt"])
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == 4
    assert summary.inputs == []
    assert summary.resources == []
    assert summary.failed.nblocked == 0
    assert summary.deferred.nblocked == 0
    assert summary.other.nblocked == 0
    assert summary.runnable.nblocked == 0
    assert summary.cyclic.nblocked == 4
    assert summary.cyclic.example == "subs1"


#
# Resources
#


async def test_resource_undefined_and_too_small(wfp: Workflow):
    scheduler, plan = await _prepare(wfp, resources="small:1")
    async with wfp.db:
        wfp.define_step(plan, "needs_undefined", resources={"ghost": 1})
        wfp.define_step(plan, "needs_small", out_paths=["small_out.txt"], resources={"small": 2})
        wfp.define_step(plan, "downstream", inp_paths=["small_out.txt"])
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == 3
    by_name = {row.name: row for row in summary.resources}
    assert by_name["ghost"].units_available is None
    assert by_name["ghost"].units_needed == 1
    assert by_name["ghost"].nblocked == 1
    assert by_name["small"].units_available == 1
    assert by_name["small"].units_needed == 2
    # Transitive: "downstream" is blocked via "needs_small", not directly on the resource.
    assert by_name["small"].nblocked == 2


#
# Determinism
#


async def test_deterministic(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    async with wfp.db:
        _declare_missing(wfp, plan, ["missing.txt"])
        wfp.define_step(plan, "step1", inp_paths=["missing.txt"])
        wfp.define_step(plan, "step2", inp_paths=["missing.txt"])
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary1 = analyze_pending(wfp)
        summary2 = analyze_pending(wfp)
    assert summary1 == summary2


#
# Ranking and truncation
#


async def test_ranking_and_truncation(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    n_files = MAX_ROWS + 5
    paths = [f"missing_{k:02d}.txt" for k in range(n_files)]
    async with wfp.db:
        _declare_missing(wfp, plan, paths)
        for k, path in enumerate(paths):
            # Descending block count: missing_00.txt blocks the most steps, so the ranked
            # table (top MAX_ROWS) must be exactly missing_00.txt .. missing_09.txt.
            nsteps = n_files - k
            for j in range(nsteps):
                wfp.define_step(plan, f"step_{k:02d}_{j:02d}", inp_paths=[path])
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert len(summary.inputs) == MAX_ROWS
    assert summary.ninputs_hidden == n_files - MAX_ROWS
    assert summary.ninputs_hidden_blocked > 0
    assert [row.path for row in summary.inputs] == [f"missing_{k:02d}.txt" for k in range(MAX_ROWS)]


async def test_hidden_inputs_shadowed_by_tie_break(wfp: Workflow):
    """Dead-end files that never win the primary-blocker tie-break still count as hidden.

    Every step here depends on all the files, so only the first one is ever a step's
    primary blocker and the rest are absent from `pend_attributed`.
    They are nevertheless files a user has to create, so they must not vanish from the
    report's hidden-row counter.
    """
    scheduler, plan = await _prepare(wfp)
    n_files = MAX_ROWS + 2
    paths = [f"missing_{k:02d}.txt" for k in range(n_files)]
    async with wfp.db:
        _declare_missing(wfp, plan, paths)
        for j in range(3):
            wfp.define_step(plan, f"step_{j}", inp_paths=paths)
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary = analyze_pending(wfp)
    assert summary.ntotal == 3
    # Exact counts are equal, so the displayed rows are the MAX_ROWS lowest-sorting files.
    assert [row.path for row in summary.inputs] == paths[:MAX_ROWS]
    assert all(row.nblocked == 3 for row in summary.inputs)
    assert summary.ninputs_hidden == n_files - MAX_ROWS
    # The hidden files block no step that the displayed ones do not already account for,
    # so the attributed lower bound is zero.
    assert summary.ninputs_hidden_blocked == 0


#
# Partition invariant
#


async def test_partition_invariant(wfp: Workflow):
    """`sum(attributed counts) + cyclic.nblocked == ntotal`, and every pending step is
    covered by exactly one bucket -- checked across several qualitatively different
    workflows built by the tests above.
    """
    scheduler, plan = await _prepare(wfp, resources="small:1")
    async with wfp.db:
        # Unavailable input.
        _declare_missing(wfp, plan, ["missing.txt"])
        wfp.define_step(plan, "step1", inp_paths=["missing.txt"], out_paths=["out1.txt"])
        wfp.define_step(plan, "step2", inp_paths=["out1.txt"])
        # Resource.
        wfp.define_step(plan, "needs_small", resources={"small": 2})
        # Failed producer.
        wfp.define_step(plan, "producer", out_paths=["prod.txt"])
        wfp.find(Step, "producer").set_state(StepState.FAILED)
        wfp.define_step(plan, "consumer", inp_paths=["prod.txt"])
        # Deferred, no blocking input.
        wfp.define_step(plan, "deferred_work")
        deferred_work = wfp.find(Step, "deferred_work")
        deferred_work.set_state(StepState.RUNNING)
        _declare_static(wfp, plan, ["side.txt"])
        amend_step(wfp, deferred_work, inp_paths=["side.txt"])
        deferred_work.set_state(StepState.PENDING, deferred=True)
        # Dynamic cycle (see test_dynamic_cycle for why this needs a product-step indirection).
        wfp.define_step(plan, "cyc1")
        wfp.define_step(plan, "cyc2")
        cyc1 = wfp.find(Step, "cyc1")
        cyc2 = wfp.find(Step, "cyc2")
        wfp.define_step(cyc1, "cyc1_sub", out_paths=["cyc_a.txt"])
        wfp.define_step(cyc2, "cyc2_sub", out_paths=["cyc_b.txt"])
        amend_step(wfp, cyc1, inp_paths=["cyc_b.txt"], out_paths=["cyc1_out.txt"])
        amend_step(wfp, cyc2, inp_paths=["cyc_a.txt"], out_paths=["cyc2_out.txt"])
        # Runnable (nothing blocks it at all).
        wfp.define_step(plan, "runnable_work")
    await _settle(wfp, scheduler)
    async with wfp.db:
        summary, attributed_totals = _analyze_pending(wfp)
    assert sum(attributed_totals.values()) + summary.cyclic.nblocked == summary.ntotal
    assert summary.runnable.nblocked == 1
    assert summary.runnable.example == "runnable_work"


#
# Scale smoke test
#


async def test_scale_smoke(wfp: Workflow):
    scheduler, plan = await _prepare(wfp)
    n_steps = 2000
    async with wfp.db:
        _declare_missing(wfp, plan, ["root_a.txt", "root_b.txt"])
        for k in range(n_steps):
            path = "root_a.txt" if k % 2 == 0 else "root_b.txt"
            wfp.define_step(plan, f"step_{k:05d}", inp_paths=[path])
    await _settle(wfp, scheduler)
    start = time.monotonic()
    async with wfp.db:
        summary = analyze_pending(wfp)
    elapsed = time.monotonic() - start
    assert summary.ntotal == n_steps
    assert len(summary.inputs) == 2
    assert sum(row.nblocked for row in summary.inputs) == n_steps
    assert elapsed < 5.0
