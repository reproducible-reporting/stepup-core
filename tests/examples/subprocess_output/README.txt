A Python step that both prints (Python-level) and shells out via subprocess.run().
This verifies that subprocess output is captured into the step's standard output and
standard error, including under the forkserver execution path.
