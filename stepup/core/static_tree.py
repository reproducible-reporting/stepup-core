# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Static trees are directories whose contents become static files when used as inputs."""

import attrs

from .trellis import Node

__all__ = ("StaticTree",)


@attrs.define
class StaticTree(Node):
    """A directory whose contents become static files when used as inputs."""

    @classmethod
    def kind(cls) -> str:
        """Lower-case prefix of the key string representing a node."""
        return "st"

    def add_source(self, source: Node, skip_cycle_check: bool = False) -> int:
        """Always raise, since a static tree does not use sources."""
        raise NotImplementedError("A static tree does not use sources.")

    def lost_product(self):
        """Do nothing, since a static tree has no cached result that could go stale.

        A static tree only declares files static;
        it is never skipped and stores nothing that a lost product would invalidate.
        It is removed by the next `Trellis.clean`, unless a new creator recycles it first.
        """
