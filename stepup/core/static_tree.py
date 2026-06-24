# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
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
"""Static trees are directories whose contents become static files when used as inputs."""

import attrs

from .cascade import Node

__all__ = ("StaticTree",)


@attrs.define
class StaticTree(Node):
    """A directory whose contents become static files when used as inputs."""

    @classmethod
    def kind(cls) -> str:
        """Lower-case prefix of the key string representing a node."""
        return "st"

    def add_supplier(self, supplier: Node) -> int:
        raise NotImplementedError("A static tree does not use suppliers.")

    def give_up(self):
        """Clean up a detached node because it loses a product node.

        Completely remove this static tree, making reuse impossible.
        """
        for product in self.products():
            product.detach()
        self.detach()
        self.clean()
        self.con.execute("DELETE FROM node WHERE i = ?", (self.i,))
