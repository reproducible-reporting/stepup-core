<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->
# Writing Integration Examples

The conventions below are load-bearing:
an example that ignores them fails in ways that do not point back at the cause
(a missing executable bit surfaces as "Permission denied",
and `STEPUP_OVERWRITE_EXPECTED=1` silently writes nothing
when the `expected_*` placeholder files do not already exist).

The authoritative description lives in the contributor README next to this file,
which is imported here so it is loaded whenever an example is edited:

@README.md
