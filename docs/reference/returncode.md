<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->
# StepUp Return Codes

The StepUp return code indicates the status of the (last) build phase.
It can be a sum of the following codes:

- `1` = the build could not be completed for a reason outside the workflow itself,
  e.g. a broken configuration file or an internal error (Python exception)
- `2` = the build was aborted by Ctrl-C or `SIGTERM`
- `4` = at least one step failed
- `8` = some workflow condition caused a warning (other than the following two)
- `16` = at least one (non-optional) step remained pending
- `32` = the scheduler was draining (not reporting pending steps)

The warning bit (`8`) is set by conditions that make the build questionable
without making it fail, such as a target that no step produces
or a [`glob()`][stepup.core.api.glob] match that no `static()` declaration justifies.
Such a build is still a successful one:
the `FAILED` bit is never set on account of a warning.

The first bit (`1`) is normally the only bit set,
because the situations that set it leave nothing else to report:
a mistake in the [configuration](configuration.md), which stops a subcommand before it starts,
or an exception escaping the director.
There is one exception, where it is combined with the outcome of a complete build:
when `STEPUP_DEBUG` is set and StepUp finds problems in `.stepup/director.log`
after the build, see [Configuration](configuration.md).

A few example combinations are:

- `0` = all steps finished successfully.
- `1` = a broken configuration file or an internal error in the director
  (never combined with other codes).
- `8` = every step succeeded, but the build reported a warning.
- `20` = at least one step failed and at least one step remained pending.
- `36` = a step failed and the scheduler was draining,
  which is what a failing step without `--keep-going` produces.
- `38` = the build was aborted by Ctrl-C (`2`) while a step was running,
  so that step counted as failed (`4`) and the scheduler was draining (`32`).

To test for a specific flag in Bash, use the bitwise AND operator `&`:

```bash
#!/usr/bin/env bash
sb
RET=$?
if [ $(($RET & 4)) -gt 0 ]; then
    echo "At least one step failed"
fi
if [ $(($RET & 8)) -gt 0 ]; then
    echo "The build reported a warning"
fi
if [ $(($RET & 16)) -gt 0 ]; then
    echo "At least one (non-optional) step remained pending"
fi
if [ $(($RET & 32)) -gt 0 ]; then
    echo "The scheduler was draining"
fi
```
