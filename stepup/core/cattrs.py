# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""StepUp instances of cattrs converters and related utilities."""

import cattrs.preconf.json
import cattrs.preconf.pyyaml
from path import Path

from .nglob import NGlobMulti, NGlobSingle

__all__ = ("json_converter", "yaml_converter")


def _register_path_hooks(converter):
    """Register structure and unstructure hooks for `path.Path` on a converter.

    `path.Path` is a `str` subclass, so it is (un)structured as a plain string,
    preserving any leading `./` or trailing `/` affixes.
    """
    converter.register_unstructure_hook(Path, str)
    converter.register_structure_hook(Path, lambda value, _: Path(value))


def _unstructure_nglob_single(ngs: NGlobSingle) -> dict:
    return {
        "pattern": ngs.pattern,
        "subs": ngs.subs,
        "results": [
            [list(key), sorted(str(path) for path in paths)]
            for key, paths in sorted(ngs.results.items())
        ],
    }


def _structure_nglob_single(data: dict, _type: type | None = None) -> NGlobSingle:
    return NGlobSingle(
        data["pattern"],
        data["subs"],
        {tuple(key): {Path(path) for path in paths} for key, paths in data["results"]},
    )


def _unstructure_nglob_multi(ngm: NGlobMulti) -> dict:
    return {
        "nglob_singles": [_unstructure_nglob_single(ngs) for ngs in ngm.nglob_singles],
    }


def _structure_nglob_multi(data: dict, _type: type | None = None) -> NGlobMulti:
    ngm = NGlobMulti(tuple(_structure_nglob_single(item) for item in data["nglob_singles"]))
    # `NGlobMulti.results` is not (un)structured explicitly: its path sets are, by invariant,
    # the very same set objects referenced by the `results` of the underlying `nglob_singles`
    # (so that in-place updates from `NGlobSingle.extend` / `.reduce` stay visible through
    # `NGlobMulti.results` without a separate write). Rebuilding it from fresh copies of the
    # serialized data, instead of through `_extend_consistent`, would break that aliasing.
    for i, ngs in enumerate(ngm.nglob_singles):
        for values in ngs.results:
            ngm._extend_consistent(i, values)
    return ngm


def _register_nglob_hooks(converter):
    """Register structure and unstructure hooks for `NGlobSingle` and `NGlobMulti`.

    Only the state that cannot be recomputed is (un)structured explicitly:
    `pattern`, `subs`, and `results` on `NGlobSingle`;
    `nglob_singles` on `NGlobMulti`.
    The remaining attributes are `init=False` fields that `attrs` derives automatically,
    or (for `NGlobMulti.results`) are recomputed from the `nglob_singles`,
    when the objects are reconstructed.
    """
    converter.register_unstructure_hook(NGlobSingle, _unstructure_nglob_single)
    converter.register_structure_hook(NGlobSingle, _structure_nglob_single)
    converter.register_unstructure_hook(NGlobMulti, _unstructure_nglob_multi)
    converter.register_structure_hook(NGlobMulti, _structure_nglob_multi)


json_converter = cattrs.preconf.json.make_converter()
yaml_converter = cattrs.preconf.pyyaml.make_converter()

_register_path_hooks(json_converter)
_register_path_hooks(yaml_converter)
_register_nglob_hooks(json_converter)
