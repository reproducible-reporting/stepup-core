A step that is detached when StepUp starts must still be marked pending
when an environment variable it uses has changed.
When the plan declares the step again, it is recycled and must rerun,
instead of keeping the output made with the old value.
