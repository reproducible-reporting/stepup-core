An OPTIONAL step is recreated with the same command (same label) as before, but
with none of its previously declared inp/out paths. This forces
Trellis.create()'s partial-recycle path: Step.can_recycle() fails because the
out_paths list no longer matches, so a fresh step row is initialized while the
old dependency edge to its (still needed) output file survives untouched --
del_sources() only cuts the step's own sources, never its sinks.

Step.initialize_row() seeds a fresh/recycled OPTIONAL step with _check_after = 0,
relying on the assumption that a dependency-edge insert will always follow and
re-flag the step for a needed-status recompute. Here, the new declaration adds
no dependency edges of its own (no inp/out/env/vol paths at all), so nothing
re-flags the step, and its _implied_need is never recomputed to account for
the still-live consumer at the far end of the stale sink edge.

The consumer lives in a completely separate sub-plan (`b/`) that never reruns
in this example, so it can never independently re-flag anything -- this
isolates the bug from a masking effect where the consumer's own recycle
happens to flag itself and that flag then propagates backward across the
stale edge, incidentally rescuing the producer.

Once `a`'s step is (correctly) recognized as needed again, it reruns and
refreshes hop2.txt on disk. The consumer in `b` never sees that refresh: since
`a` no longer declares hop2.txt as an output, that file node has no producer
anymore, and `b`'s own (untouched) declared input now points at an orphaned
file -- reported as a detached input in the pending report's "Unavailable inputs"
table, the same diagnostic used by e.g. the `undeclared_detached` example.
That part is a separate, pre-existing
piece of behavior (dropping a still-referenced output declaration is not
something StepUp reconciles automatically), unrelated to the OPTIONAL-step bug
this example targets, so phase 2 only checks that `a`'s own step reran.
