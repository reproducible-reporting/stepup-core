This example demonstrates the runpyep action using the subprocess fallback path.

It is the counterpart to the runpyep example, but with fork-runpy disabled.
When fork-runpy = false, WorkThread.runpyep() falls back to runexec_verbose()
instead of using the forkserver, running the entry point as a regular subprocess.

The expected graph is identical to the runpyep example — the action name and step
state are the same regardless of which execution path is taken internally.
