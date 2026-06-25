A step may declare a static file and then use it as input. Changing that input reruns the
step without a spurious cyclic or pending dependency.
