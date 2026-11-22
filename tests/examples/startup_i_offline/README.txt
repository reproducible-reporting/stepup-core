Two steps that track `STEPUP_STARTUP_I` exchange data with a stand-in for a remote server,
the directory `external/`: `pull.py` downloads `data.txt` and `push.py` uploads `report.txt`,
which another step builds from `data.txt` and `template.txt`.
With `--offline`, `STEPUP_STARTUP_I` keeps its value,
so neither step runs merely because StepUp started.
A step that runs for another reason finds `STEPUP_BUILD_OFFLINE=1` and leaves the external data alone.
The next start without `--offline` looks at the external data again.
