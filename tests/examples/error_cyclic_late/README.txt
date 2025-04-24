A simple example of a cyclic dependency: `plan.py` declares the static file `README.md`,
and then wants to use it as an input through the `amend` function.
