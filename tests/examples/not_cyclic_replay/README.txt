A step may declare a static file and then use it as input. Touching the input and deleting
the output replays the step to rebuild the output, without a spurious cyclic dependency.
