#!/usr/bin/env python
"""Run one example and amend (volatile) outputs."""

import subprocess
import sys

from path import Path

from stepup.core.api import amend
from stepup.core.file import FileState
from stepup.core.workflow import Workflow


def main():
    path_main, root = sys.argv[1:]

    workdir, name_main = Path(path_main).splitpath()
    subprocess.run(f"./{name_main}", cwd=workdir, check=False)

    root = Path(root)
    path_stepup = root / ".stepup/"
    path_workflow = path_stepup / "workflow.mpk.xz"
    workflow = Workflow.from_file(path_workflow)
    out = [path_stepup]
    vol = [path_workflow]
    for file in workflow.get_files():
        state = file.get_state(workflow)
        if state == FileState.BUILT:
            out.append(root / file.path)
        elif state == FileState.VOLATILE:
            vol.append(root / file.path)
    amend(out=out, vol=vol)


if __name__ == "__main__":
    main()
