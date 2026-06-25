An amended input (inp2.txt) is itself produced by another step. Editing it out of band
before a restart is reverted by its producer step on restart, so the amending step
(work.py) stays skipped.
