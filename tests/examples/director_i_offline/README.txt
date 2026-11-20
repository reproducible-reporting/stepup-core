Two steps that track `STEPUP_DIRECTOR_I` exchange data with a stand-in remote,
the directory `remote/`: `pull.py` downloads `data.txt` and `push.py` uploads `report.txt`,
which another step builds from `data.txt` and `template.txt`.
With `--offline`, `STEPUP_DIRECTOR_I` keeps its value,
so neither step runs merely because StepUp started.
A step that runs for another reason finds `STEPUP_BUILD_OFFLINE=1` and leaves the remote alone.
The next start without `--offline` looks at the remote again.
