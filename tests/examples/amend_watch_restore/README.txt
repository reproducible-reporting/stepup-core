An amended input (inp2.txt) is itself produced by another step. Editing it out of band
while StepUp is watching is reverted by its producer step, so the amending step (work.py)
stays skipped.
