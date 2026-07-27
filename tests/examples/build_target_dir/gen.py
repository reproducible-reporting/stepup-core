#!/usr/bin/env python3
"""Consumer that discovers its inputs one at a time, mimicking typst's dependency discovery.

Each invocation amends the next undiscovered input in order; `amend()` raises when the
amended input is not yet available, which StepUp turns into a postponed rerun once it is
(see `stepup.core.api.amend`'s docstring). `invocations.txt` counts how many times this
step actually ran, to make the flagship claim of `build_target_dir.md` observable: with
the producers already elevated by a directory target on `out/`, they are all built before
this step ever runs, so it succeeds on its first invocation instead of one retry per input.
"""

from path import Path

from stepup.core.api import amend
from stepup.core.call import driver

INPUTS = ["out/a.txt", "out/b.txt", "out/c.txt"]


def run():
    invocations_path = Path("invocations.txt")
    count = int(invocations_path.read_text()) + 1 if invocations_path.is_file() else 1
    invocations_path.write_text(f"{count}\n")

    parts = []
    for inp in INPUTS:
        amend(inp=[inp])
        parts.append(Path(inp).read_text().strip())
    Path("out/result.txt").write_text("".join(parts) + "\n")


if __name__ == "__main__":
    driver()
