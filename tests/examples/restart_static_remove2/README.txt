This test case first introduces a static file, which is then used by the plan.
Then the static file is removed and the plan is changed accordingly,
after which StepUp is restarted.
It should not complain about the missing file.
