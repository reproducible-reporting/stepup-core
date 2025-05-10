# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Driver function to facilitate writing scripts that adhere to StepUp's call protocol.

See [Call Protocol](../getting_started/call.md) for more details.
"""

import argparse
import inspect
import json
import pickle
import sys
from typing import Any

from path import Path

__all__ = ("driver",)


def driver(obj: Any = None):
    """Implement call protocol.

    The most common usage is to call `driver()` from a script that defines a `run()` function, e.g.:

    ```python
    #!/usr/bin/env python3
    from stepup.core.call import driver

    def run(a: int, b: int) -> int:
        return a + b

    if __name__ == "__main__":
        driver()
    ```

    Parameters
    ----------
    obj
        When not provided, the namespace of the module where `driver` is defined
        will be searched for the name 'run' to implement the call protocol.
        When an object is given as a parameter, its attributes are searched instead.
    """
    frame = inspect.currentframe().f_back
    script_path = Path(frame.f_locals["__file__"]).relpath()
    if obj is None:
        # Get the calling module and use it as obj
        module_name = frame.f_locals["__name__"]
        obj = sys.modules.get(module_name)
        if obj is None:
            raise ValueError(
                f"The driver must be called from an imported module, got {module_name}"
            )
    args = parse_args(script_path)

    # Load the keyword arguments
    if args.json_inp is not None:
        kwargs = json.loads(args.json_inp)
    elif args.path_inp is None:
        kwargs = {}
    elif args.path_inp.suffix == ".json":
        with open(args.path_inp) as fh:
            kwargs = json.load(fh)
    elif args.path_inp.suffix == ".pickle":
        with open(args.path_inp, "rb") as fh:
            kwargs = pickle.load(fh)
    else:
        raise NotImplementedError(f"Unsupported input file format: {args.path_inp.suffix}")

    # Call the run function
    run = getattr(obj, "run", None)
    if run is None:
        raise AttributeError("The module must define a 'run' function")
    # Filter kwargs to only include those accepted by the run function
    run_signature = inspect.signature(run)
    filtered_kwargs = {k: v for k, v in kwargs.items() if k in run_signature.parameters}
    # Turn inp, out and vol kwargs into lists of Path instances.
    for key in ("inp", "out", "vol"):
        key_paths = filtered_kwargs.get(key)
        if isinstance(key_paths, str):
            filtered_kwargs[key] = Path(key_paths)
        elif isinstance(key_paths, list):
            filtered_kwargs[key] = [Path(p) for p in key_paths]
    # Call the run function with the filtered kwargs.
    result = run(**filtered_kwargs)

    # Use a local import because the API is only needed when the driver is called.
    from .api import amend

    # Amend inputs using imported modules.
    # This goes a bit against good practice, in the sense that amending should be done early.
    # It is acceptable here because the driver would fail anyway if the imports are not available.
    # By amending after calling the driver, we also pick up local imports, if any.
    out = []
    if not (result is None or args.path_out is None) and args.amend_out:
        out.append(args.path_out)
    amend(out=out)

    # Save the result if not None
    if result is not None:
        if args.path_out is None:
            raise ValueError("The output path is mandatory when the run function returns a value.")
        if args.path_out.suffix == ".json":
            with open(args.path_out, "w") as fh:
                json.dump(result, fh)
                fh.write("\n")
        elif args.path_out.suffix == ".pickle":
            with open(args.path_out, "wb") as fh:
                pickle.dump(result, fh)
        else:
            raise NotImplementedError(f"Unsupported output file format: {args.path_out.suffix}")


def parse_args(script_path: str) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog=script_path, description=f"StepUp call driver for {script_path}."
    )
    parser.add_argument("json_inp", nargs="?", type=str, help="JSON string input (if any)")
    parser.add_argument("--out", dest="path_out", type=Path, help="Output path (if any)")
    parser.add_argument("--inp", dest="path_inp", type=Path, help="Input path (if any)")
    parser.add_argument("--amend-out", action="store_true", help="Amend the output file")
    args = parser.parse_args()
    if None not in (args.json_inp, args.path_inp):
        raise ValueError("Cannot provide both JSON input string and an input file")
    return args
