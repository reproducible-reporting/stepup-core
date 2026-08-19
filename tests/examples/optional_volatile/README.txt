This example tests the file states of an optional step's outputs after it is reverted.

An optional step declares both a regular and a volatile output,
and a non-optional consumer of the regular output makes it needed.
When that consumer disappears, `revert_optional` puts the step back to PENDING
and removes both outputs from disk.

The regular output then returns to PLANNED,
but the volatile one must stay VOLATILE.
VOLATILE is the only state in its role,
so resetting it would migrate it into the BUILT role,
where `Step.out_paths()` counts it as a regular output of the step.

The third phase restores the consumer within the same director session,
which is the window where the wrong state used to be observable:
the step becomes needed again without being redeclared.
