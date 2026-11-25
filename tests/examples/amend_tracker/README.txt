This example shows how track_amend() records the dependencies amended by a part of a step.

The script sub/work.py demonstrates the following:

- An input amended before the block is recorded when it is amended again inside the block.
- An amendment after changing directory is recorded relative to the directory
  at the start of the block.
- An amendment in a nested block is recorded by the inner and the outer tracker.
- An amendment after the block is not recorded.
- The sets of the tracker can be extended in place, e.g. with an input that need not be amended.

The tracked dependencies are written to current_tracked_info.json with dump_step_info().
