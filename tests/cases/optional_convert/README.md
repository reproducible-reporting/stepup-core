Steps can be made optional, meaning that they are only executed when their outputs are
(indirectly) needed by non-optional steps.

A typical use case is to define steps for conversions of a large number of raw files,
if which only a few are really needed. It can be tedious to figure out manually which ones
are needed, in which case these conversions can be made optional.

This example follows the conversion pattern, but uses copy commands for the sake of simplicity.
