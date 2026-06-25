work.py declares input.txt as static and uses it as input (no cyclic dependency).
Changing the input reruns the step without a spurious pending dependency.
