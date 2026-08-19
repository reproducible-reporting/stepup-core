A restart with no changes skips the plan step (it neither reruns nor re-registers
its glob() pattern), yet the end-of-phase check still reports data/a.txt as an
unjustified match. This is because find_glob_violations() reads the persisted nglob
table rather than what actually ran this phase, which is what makes the check
idempotent across restarts.
