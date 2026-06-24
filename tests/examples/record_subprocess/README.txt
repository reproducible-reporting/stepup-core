Verifies that a wrapper step can record the exact subprocess invocation it makes
via run_subprocess, and that the invocation (command line, workdir, environment overlay
and return code) is stored in the step_subprocess table for archival purposes.
