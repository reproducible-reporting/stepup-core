This test case first introduces a static file and step using it.
Then the static file and the step are removed during watch phase,
after which the runner starts again.
It should not complain about the missing file.
