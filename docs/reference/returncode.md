# StepUp Return Codes

The StepUp return code indicates the status of the (last) run phase.
It can be a sum of the following codes:

- `1` = internal error (Python exception)
- `2` = at least one step failed
- `4` = at least one step remained pending
- `8` = at least one step was still runnable

A few example combinations are:

- `0` = all steps finished successfully.
- `1` = internal error (never combined with other codes).
- `6` = at least one step failed and at least one step remained pending.
- `12` = some steps remain pending and some steps are runnable when StepUp is restarted.

To test for a specific flag in Bash, use the bitwise AND operator `&`:

```bash
#!/usr/bin/env bash
stepup boot
RET=$?
if [ $(($RET & 2)) -gt 0 ]; then
    echo "At least one step failed"
fi
if [ $(($RET & 4)) -gt 0 ]; then
    echo "At least one step remained pending"
fi
if [ $(($RET & 8)) -gt 0 ]; then
    echo "At least one step was still runnable"
fi
```
