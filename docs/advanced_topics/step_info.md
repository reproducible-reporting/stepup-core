---
description: >-
  Reuse the paths that an API function constructed for a step,
  through the StepInfo object it returns, instead of rebuilding them in plan.py.
---

# `StepInfo` Objects

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

!!! note

    This feature was introduced in StepUp 1.3.0.

The [`step()`][stepup.core.api.step] function always returns
an instance of [`StepInfo`][stepup.core.stepinfo.StepInfo].
This object holds arguments used to define the step,
which may be useful for creating follow-up steps.
All API functions that create a step by calling the [`step()`][stepup.core.api.step] function
also return the [`StepInfo`][stepup.core.stepinfo.StepInfo] object.

Especially for higher-level API functions that create more advanced steps,
such as the ones in [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/),
it can be convenient to access the paths constructed inside the API function
instead of reconstructing them manually in your `plan.py` script.

The `StepInfo` object has `inp`, `env`, `out` and `vol` attributes,
corresponding to the arguments passed into the `step()` function.
The main difference to the arguments passed in
is that environment variables are substituted in the paths.

In addition, `StepInfo` has three methods: `filter_inp()`, `filter_out()` and `filter_vol()`,
which can be used to get a subset of paths.
These functions take the same arguments as those of the [`glob()`][stepup.core.api.glob] function
and also return a [`NamedGlob`][stepup.core.nglob] instance.

Relative paths in `inp`, `out` and `vol` are relative to `info.workdir`,
which is itself relative to the current directory, unless it is absolute.
Hence, `info.workdir / path` is the path of a dependency relative to the current directory.
To get paths relative to another directory, use
[`rebase()`][stepup.core.stepinfo.StepInfo.rebase],
e.g. `info.rebase(".")` for paths relative to the current directory.

A `StepInfo` only contains the dependencies known when the step was defined.
Dependencies amended while the step runs can be collected with
[`track_amend()`][stepup.core.api.track_amend]
or [`get_amended_info()`][stepup.core.api.get_amended_info],
and combined with [`merge()`][stepup.core.stepinfo.StepInfo.merge],
e.g. `get_info().merge(get_amended_info())`
for the declared and amended dependencies of the current step.

The functions [`dump_step_info()`][stepup.core.stepinfo.dump_step_info]
and [`load_step_info()`][stepup.core.stepinfo.load_step_info]
save and load `StepInfo` objects in JSON files,
which can be loaded from any current directory, also after the directory tree was moved.

## Example

Let's assume you are using a library with an API function `create_fancy_pdf`,
which takes a source directory as input and outputs a PDF file.
(This is a hypothetical example for illustrative purposes.)
You can find the full list of inputs and the output as follows:

```python
from stepup.core.api import static, copy
from stepup.fancy.api import create_fancy_pdf  # hypothetical API function

# Plan the creation of the fancy PDF
static("source/")
info = create_fancy_pdf("source/")

# Copy files related to the fancy PDF, e.g. to publish them or back up files.
copy(info.filter_out("*.pdf").single, "../public/")
for path_inp in info.inp:
    copy(path_inp, "../backup")
```
