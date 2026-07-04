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

    def add_supplier(self, supplier: Node, skip_cycle_check: bool = False) -> int:
        raise NotImplementedError("A static tree does not use suppliers.")

    def give_up(self):
        """Clean up a detached node because it loses a product node.

        Completely remove this static tree, making reuse impossible.
        """
        for product in self.products():
            product.detach()
        self.detach()
        self.clean()
        self.db.execute("DELETE FROM node WHERE i = ?", (self.i,))
