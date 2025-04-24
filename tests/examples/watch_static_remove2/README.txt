This test case first introduces a static file, which is then used by the plan.
Then the static file is removed and the plan is changed accordingly,
after which the runner starts again.
It should not complain about the missing file.
