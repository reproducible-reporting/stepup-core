# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""StepUp instances of cattrs converters and related utilities."""

import cattrs.preconf.json
import cattrs.preconf.pyyaml
from path import Path

from .nglob import NamedGlob

__all__ = ("json_converter", "yaml_converter")


def _register_path_hooks(converter):
    """Register structure and unstructure hooks for `path.Path` on a converter.

    `path.Path` is a `str` subclass, so it is (un)structured as a plain string,
    preserving any leading `./` or trailing `/` affixes.
    """
    converter.register_unstructure_hook(Path, str)
    converter.register_structure_hook(Path, lambda value, _: Path(value))


def _unstructure_named_glob(ng: NamedGlob) -> dict:
    return {
        "pattern": ng.pattern,
        "subs": ng.subs,
        "results": [
            [list(key), sorted(str(path) for path in paths)]
            for key, paths in sorted(ng.results.items())
        ],
    }


def _structure_named_glob(data: dict, _type: type | None = None) -> NamedGlob:
    return NamedGlob(
        data["pattern"],
        data["subs"],
        {tuple(key): {Path(path) for path in paths} for key, paths in data["results"]},
    )


def _register_nglob_hooks(converter):
    """Register structure and unstructure hooks for `NamedGlob`.

    Only the state that cannot be recomputed is (un)structured explicitly:
    `pattern`, `subs`, and `results`.
    The remaining attributes are `init=False` fields that `attrs` derives automatically
    when the object is reconstructed.
    """
    converter.register_unstructure_hook(NamedGlob, _unstructure_named_glob)
    converter.register_structure_hook(NamedGlob, _structure_named_glob)


json_converter = cattrs.preconf.json.make_converter()
yaml_converter = cattrs.preconf.pyyaml.make_converter()

_register_path_hooks(json_converter)
_register_path_hooks(yaml_converter)
_register_nglob_hooks(json_converter)
