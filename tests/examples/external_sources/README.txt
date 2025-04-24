This is a simple demonstration of the use of external sources in StepUp.
The directory `project` contains a simple StepUp projects
with a script that uses an external source.
The external source is a Python script in the directory `pkgs`,
which is included in the `PYTHONPATH`.
By settings `STEPUP_EXTERNAL_SOURCES` to `pkgs`,
StepUp's `script` and `call` drivers will treat imports from modules in `pkgs`
as dependencies to be tracked.
To test this feature, there are two versions of `helper.py` in `pkgs`.
Switching from one version to the other
will cause StepUp to re-run the script using `helper.py`
