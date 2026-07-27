This example exercises the hash-checkable exemption from hold(): a plan step holds two
children, one whose command hash is unchanged from a prior run (must SKIP) and one that has
never run before and sleeps for a noticeable time once dispatched (must actually run).

It checks, from inside the still-active hold() block, that the unchanged child has already
been skipped -- proving a hash-checkable step is verified promptly even while held -- while
the slow child has not even started, proving that an actual rerun stays fully gated by the
hold until release().
