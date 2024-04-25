StepUp supports scripts that combine planning and execution.
The `driver` function provided in `stepup.script` handles all the interaction with the StepUp server.

This example shows how to run multiple instances of the work.py script with different parameters.
The cases are taken from a config file settings.py, which is imported in `generate_cases`.
Because of this local import, the file `settings.py` only is a dependency of the planning of the
`work.py` script, not the run part, which is sometimes convenient, e.g. to add new cases without
having to rerun the ones that remained the same.
