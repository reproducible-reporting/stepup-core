This example illustrates the following chain of events:

1. When a file changes, steps using it (consumers of the file) become pending.

   > In this example, file `config.json` is modified.
   > This is a model for a changing end-user configuration settings of a build.

2. Outputs of the pending step become pending too.

   > Here, `./use_config.py` becomes pending because it uses the config file.

3. The steps created in the newly pending steps become stale.
  (They may or may not be created after a rerun, which cannot be determined upfront.)

   > Here, two steps are created in `use_config.py`, one of which uses information from `config.json`.

4. The outputs of stale steps also become stale.

   > The stale files in this example are the log file defined in `config.json` and `report.txt`.

5. Steps using stale files as input become pending, and their outputs become pending too.

   > Here, the step copying `report.txt` to `copy.txt` becomes pending.
