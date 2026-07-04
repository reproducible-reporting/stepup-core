# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""StepUp instances of cattrs converters and related utilities."""

import cattrs.preconf.json
import cattrs.preconf.pyyaml
from path import Path

__all__ = ("json_converter", "yaml_converter")


def _register_path_hooks(converter):
    """Register structure and unstructure hooks for `path.Path` on a converter.

    `path.Path` is a `str` subclass, so it is (un)structured as a plain string,
    preserving any leading `./` or trailing `/` affixes.
    """
    converter.register_unstructure_hook(Path, str)
    converter.register_structure_hook(Path, lambda value, _: Path(value))


json_converter = cattrs.preconf.json.make_converter()
yaml_converter = cattrs.preconf.pyyaml.make_converter()

_register_path_hooks(json_converter)
_register_path_hooks(yaml_converter)
