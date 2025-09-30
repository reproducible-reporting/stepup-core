This example reproduces a problem with the automatic dependency tracking of runpy steps.
When a subclass of types.ModuleType is created with an ad hoc __file__ attribute,
StepUp should detect that this file is not a dependency of the script.

A real-life example of this problem are the _classes.py and _ops.py "modules" in PyTorch.
