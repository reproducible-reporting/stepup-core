# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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
"""The `Workflow` is a `Cascade` subclass with more concrete node implementations."""

import asyncio
import json
import os
import textwrap
from collections import Counter
from collections.abc import Collection
from typing import Self, cast

import attrs

from .assoc import Assoc, many_to_one
from .cascade import Cascade, Node, get_kind
from .deferred_glob import DeferredGlob
from .exceptions import GraphError
from .file import File, FileState
from .hash import ExtendedStepHash, FileHash
from .nglob import NGlobMulti
from .step import Mandatory, Step, StepState
from .utils import lookupdict, myparent

__all__ = ("Workflow",)


@attrs.define
class Workflow(Cascade):
    # Steps ready for scheduling and execution.
    job_queue: asyncio.queues = attrs.field(init=False, factory=asyncio.Queue)

    # Directories to be (un)watched
    dir_queue: asyncio.queues = attrs.field(init=False, factory=asyncio.Queue)

    # This event is set when the scheduler should check the job_queue again.
    job_queue_changed: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    # The workflow_changed flag is set True in the run phase methods, right before they start
    # changing the graph. Errors raised afterwards result in an inconsistent graph.
    graph_changed: bool = attrs.field(init=False, default=False)

    # A list of files and directories that can be deleted.
    # This list contains BUILT files node with file hashes that were removed from the graph.
    to_be_deleted: list[tuple[str, FileHash | None]] = attrs.field(init=False, factory=list)

    # Node attributes stored in an Assoc to facilitate reverse lookups:
    file_states: "Assoc[str, FileState]" = attrs.field(init=False, factory=many_to_one)
    step_states: "Assoc[str, StepState]" = attrs.field(init=False, factory=many_to_one)
    step_mandatory: "Assoc[str, Mandatory]" = attrs.field(init=False, factory=many_to_one)

    # All keys using nglobs, used to lookup steps after watch phase that need to be re-executed.
    step_keys_with_nglob: set[str] = attrs.field(init=False, factory=set)

    #
    # Initialization and serialization
    #

    @classmethod
    def structure(cls, state) -> Self:
        workflow = cast(Workflow, super().structure(state))
        # Derive recording objects from graph.
        for step in workflow.get_steps():
            if step.get_state(workflow) == StepState.SUCCEEDED:
                step.update_recording(workflow)
        return workflow

    def check_consistency(self):
        super().check_consistency()
        # Check whether the initial graph satisfies all constraints.
        for node_key in self.nodes:
            creator_key = self.get_creator(node_key)
            if get_kind(creator_key) == "step" and node_key == creator_key:
                raise ValueError("A step cannot be its own creator")
            if get_kind(node_key) == "file":
                parent_key = self.get_parent_key(node_key[5:], allow_orphan=True)
                if not (parent_key is None or parent_key in self.suppliers.get(node_key, ())):
                    raise ValueError(f"File is not consumer of its parent: {node_key}")

    @staticmethod
    def default_node_classes() -> dict[str, type[Node]]:
        result = Cascade.default_node_classes()
        result["file"] = File
        result["step"] = Step
        result["dg"] = DeferredGlob
        return result

    def discard_recordings(self):
        for step in self.get_steps():
            step.discard_recording()

    def dissolve(self, reduce_step_hashes: bool = False):
        """Remove everything that is irrelevant for skipping steps that need no re-execution.

        This is called in two situations:
        - After loading a workflow from disk and before starting the runner.
          One must assume all files may have changed without getting details from watch phase.
          Any uncertain information is discarded.
        - After certain errors were raised in run phase (and after saving the workflow to disk).
          Some errors may render the graph inconsistent, which mandates this type of cleanup,
          to avoid that inconsistencies hamper the next run iteration.

        Parameters
        ----------
        reduce_step_hashes
            When given, ExtendedStepHash instances are replaced by simpler StepHash instances.
        """
        # Orphan all nodes
        for node_key in self.nodes:
            if node_key not in ("root:", "vacuum:"):
                self.orphan(node_key)
        # Discard recording of steps that use one or more (named) globs.
        # While StepUp was not running, files could have been added or removed,
        # so the file matching patterns need to be checked again.
        for step_key in self.step_keys_with_nglob:
            step = self.get_step(step_key)
            step.discard_recording()
            if reduce_step_hashes and isinstance(step._hash, ExtendedStepHash):
                step._hash = step.hash.reduce()
        # Remove supplier and consumers of all steps to potentially recover from previously failed
        # runs without completely discarding the graph.
        # Steps normally have no direct consumers, but better safe than sorry.
        # Note that file suppliers and consumers are kept in place, because parent directories
        # must be suppliers of their contents.
        for step_key in self.kinds.get("step", ()):
            self.suppliers.discard(step_key)
            self.consumers.discard(step_key)

    def _format_dot_generic(self, arrowhead: str, include_dirs: bool, edges: Assoc):
        lines = [
            "strict digraph {",
            "  graph [rankdir=BT bgcolor=transparent]",
            "  node [penwidth=0 colorscheme=set39 style=filled fillcolor=5]",
            f"  edge [color=dimgray arrowhead={arrowhead}]",
        ]
        lookup = lookupdict()
        for key, node in self.nodes.items():
            if key not in edges and key not in edges.inverse:
                continue
            if not include_dirs and key.startswith("file:") and key.endswith("/"):
                continue
            label = node.key[:-1] if node.key.endswith(":") else node.key[node.key.find(":") + 1 :]
            label = json.dumps(textwrap.fill(label, 20))
            if node.kind == "step":
                props = ""
            elif node.kind == "file":
                props = " shape=rect fillcolor=9"
            elif node.kind == "dg":
                props = " shape=octagon fillcolor=7"
            else:
                props = " shape=hexagon fillcolor=6"
            lines.append(f"  {lookup[key]} [label={label}{props}]")
            for other in edges.get(key, ()):
                if other != key:
                    lines.append(f"  {lookup[key]} -> {lookup[other]}")
        lines.append("}")
        return "\n".join(lines)

    def format_dot_creator(self):
        """Return the creator-product graph in GraphViz DOT format."""
        return self._format_dot_generic("empty", True, self.products)

    def format_dot_supplier(self):
        """Return the supplier-product graph in GraphViz DOT format."""
        return self._format_dot_generic("normal", False, self.consumers)

    #
    # Type-annotated and type-checked node access
    #

    def get_file(self, file_key: str) -> File:
        if not file_key.startswith("file:"):
            raise TypeError(f"The key given to get_file is not a file: {file_key}")
        node = self.nodes.get(file_key)
        if not (node is None or isinstance(node, File)):
            raise TypeError(f"The file has the wrong type {type(node)}: {file_key}")
        return node

    def get_files(self, include_orphans: bool = False) -> list[File]:
        return self.get_nodes("file", include_orphans)

    def get_step(self, step_key: str) -> Step:
        if not step_key.startswith("step:"):
            raise TypeError(f"The key given to get_step is not a step: {step_key}")
        node = self.nodes.get(step_key)
        if not (node is None or isinstance(node, Step)):
            raise TypeError(f"The step has the wrong type {type(node)}: {step_key}")
        return node

    def get_steps(self, include_orphans: bool = False) -> list[Step]:
        return self.get_nodes("step", include_orphans)

    def get_deferred_glob(self, dg_key: str) -> DeferredGlob:
        if not dg_key.startswith("dg:"):
            raise TypeError(
                f"The key given to get_deferred_nglob is not a deferred nglob: {dg_key}"
            )
        node = self.nodes.get(dg_key)
        if not (node is None or isinstance(node, DeferredGlob)):
            raise TypeError(f"The deferred nglob has the wrong type {type(node)}: {dg_key}")
        return node

    def get_deferred_globs(self, include_orphans: bool = False) -> list[DeferredGlob]:
        return self.get_nodes("dg", include_orphans)

    #
    # Workflow inspection
    #

    def get_file_counters(self) -> Counter[FileState, int]:
        """Return counters for FileState."""
        return Counter(
            {
                file_state: len(file_keys)
                for file_state, file_keys in self.file_states.inverse.items()
            }
        )

    def get_step_counters(self) -> Counter[StepState, int]:
        """Return counters for StepState."""
        return Counter(
            {
                step_state: len(step_keys)
                for step_state, step_keys in self.step_states.inverse.items()
            }
        )

    #
    # Run phase
    #

    def _check_step_key(self, step_key: str, argname: str, allow_root=False):
        """Check the creator or step key in the methods below."""
        if not isinstance(step_key, str):
            raise ValueError(f"{argname} must be a string, got: {type(step_key)}")
        if step_key not in self.nodes:
            raise ValueError(f"Unknown {argname}: '{step_key}'")
        if self.is_orphan(step_key):
            raise ValueError(f"{argname} is orphan: '{step_key}'")
        if allow_root and step_key == "root:":
            return
        if not (step_key.startswith("step:") or step_key.startswith("dg:")):
            allowed = "step, dg or root" if allow_root else "step or dg"
            raise ValueError(f"{argname} is not a {allowed}: '{step_key}'")

    def supply_parent(self, file: File) -> tuple[str | None, bool, bool]:
        parent_path = myparent(file.path)
        if parent_path is None:
            return None, True, False
        return self.supply_file(file.key, parent_path, new=False)

    def matching_deferred_glob(self, path: str) -> DeferredGlob | None:
        dgs = []
        for dg in self.get_deferred_globs():
            if dg.ngm.may_match(path):
                dgs.append(dg)
        if len(dgs) > 1:
            raise GraphError(f"Multiple deferred globs match: {path}")
        if len(dgs) == 1:
            return dgs[0]
        return None

    @staticmethod
    def always_static(path: str) -> bool:
        return path in ["./", "/"] or path == "../" * (len(path) // 3)

    def supply_file(self, step_key: str, path: str, new: bool = True) -> tuple[str, bool, bool]:
        """Find an existing file or create on orphan file, and make it a supplier of step_key.

        Parameters
        ----------
        step_key
            The step or file to supply to.
        path
            The file that should supply to the step.
        new
            When True the (file, step) relationship must be new.
            If not, a ValueError is raised.

        Returns
        -------
        file_key
            The key of the created / recycled file.
        available
            True when the requested path is BUILT or STATIC.
        new_relation
            True when the requested relation is new.
        """
        file_key = f"file:{path}"
        available = False
        if file_key not in self.nodes or self.is_orphan(file_key):
            if self.always_static(path):
                self.declare_static("root:", [path])
            else:
                dg = self.matching_deferred_glob(path)
                if dg is None:
                    file = File(path)
                    self.create(file, "vacuum:")
                    self.supply_parent(file)
                    file.set_state(self, FileState.PENDING)
                else:
                    dg.ngm.extend([path])
                    self.declare_static(dg.key, [path])
                    available = True
        else:
            state = self.get_file(file_key).get_state(self)
            if state == FileState.VOLATILE:
                raise GraphError(f"Input is volatile: {path}")
            available = state in (FileState.BUILT, FileState.STATIC)
        new_relation = file_key not in self.suppliers.get(step_key, ())
        if new_relation:
            self.supply(file_key, step_key)
        elif new:
            raise GraphError(f"Supplying file already exists: {path}")
        return file_key, available, new_relation

    def create_file(self, creator_key: str, path: str, file_state: FileState) -> str:
        """Create (or recycle) a file with a PENDING or VOLATILE file state.

        Parameters
        ----------
        creator_key
            The creating step, deferred glob.
        path
            The (normalized path). Directories must have trailing slashes.
        file_state
            The desired file state if any.
            (None for supplying files, not None in all other cases)

        Returns
        -------
        file_key
            The key of the created / recycled file.
        """
        if file_state not in (FileState.STATIC, FileState.PENDING, FileState.VOLATILE):
            raise ValueError("Can only create a file with state STATIC, PENDING or VOLATILE.")
        if file_state == FileState.VOLATILE and path.endswith(os.sep):
            raise GraphError("A volatile output cannot be a directory.")
        if not (creator_key.startswith("dg:") or self.matching_deferred_glob(path) is None):
            raise GraphError("Cannot manually add a file that matches a deferred glob.")
        if file_state != FileState.STATIC and self.always_static(path):
            raise GraphError(f"Path is created as {file_state} but must be static: {path}")
        file_key = f"file:{path}"
        file = self.get_file(file_key)
        if not (file is None or self.is_orphan(file_key)):
            raise GraphError(f"File was already created: {path}")
        if file_state == FileState.VOLATILE and len(self.consumers.get(file_key, ())) > 0:
            raise GraphError(f"An input to an existing step cannot be volatile: {path}")
        file = File(path)
        self.create(file, creator_key)
        parent_key, _, _ = self.supply_parent(file)
        file.set_state(self, file_state)
        if file_state == FileState.STATIC:
            if not (
                parent_key is None or self.get_file(parent_key).get_state(self) == FileState.STATIC
            ):
                raise GraphError(f"Static path does not have a parent path node: {path}")
            file.release_pending(self)
        else:
            self.supply(creator_key, file_key)
        return file_key

    def get_parent_key(self, path: str, allow_orphan: bool = False) -> str | None:
        """Get the key of the parent File object, if relevant for consistency checking.

        Parameters
        ----------
        path
            The path whose parent is requested.
        allow_orphan
            Set to True if orphan keys are acceptable.

        Raises
        ------
        ValueError
            When the path or parent path are non-trivial and the parent path key could not be found.

        Returns
        -------
        parent_key
            This is None when the path or parent path are always static.
            In all other cases, the key of the parent path is returned.
        """
        if self.always_static(path):
            return None
        parent_path = myparent(path)
        if self.always_static(parent_path):
            return None
        parent_key = f"file:{parent_path}"
        if parent_key not in self.nodes or (self.is_orphan(parent_key) and not allow_orphan):
            raise ValueError(f"Path does not have a parent path node: {path}")
        return parent_key

    def declare_static(self, creator_key: str, paths: Collection[str]) -> list[str]:
        """Declare a file as static, i.e. created manually, not built by a step.

        Parameters
        ----------
        creator_key
            The node creating this file (or None if not known).
        paths
            The locations of the files or directories (ending with /).

        Returns
        -------
        file_keys
            The keys of the static files.
        """
        if isinstance(paths, str):
            raise TypeError("The paths argument cannot be a string.")
        self._check_step_key(creator_key, "creator_key", allow_root=True)
        # Run some checks early
        paths = sorted(set(paths))
        for path in paths:
            # Check that parent is static (unless path is './').
            # This is only possible upfront for manually added files,
            # Not for files added through a deferred glob,
            # because adding these may require adding their parent paths.
            if not creator_key.startswith("dg:"):
                try:
                    parent_key = self.get_parent_key(path)
                    if not (
                        parent_key is None or self.file_states.get(parent_key) == FileState.STATIC
                    ):
                        raise GraphError(f"Static path has no static parent path node: {path}")
                except ValueError as exc:
                    parent_path = myparent(path)
                    if parent_path not in paths:
                        raise GraphError(f"Static path has no parent path node: {path}") from exc

            file_key = f"file:{path}"
            if file_key in self.nodes:
                if file_key in self.nodes and not self.is_orphan(file_key):
                    raise GraphError(f"Static path already exists: {path}")
                self.check_cyclic(creator_key, file_key)
        # Make the actual changes
        self.graph_changed = True
        return [self.create_file(creator_key, path, FileState.STATIC) for path in paths]

    def check_inputs_outputs(
        self,
        step_key: str,
        inp_paths: list[str],
        out_paths: list[str],
        vol_paths: list[str],
    ):
        """Check whether the new inputs and outputs can be used before making changes.

        Because no changes are made yet, a GraphError can be raised when a problem is detected.
        These checks overlap with similar checks while making changes, but are just raised earlier.

        Parameters
        ----------
        step_key
            The step for which the new inputs and outputs are intended.
        inp_paths
            A list of input paths to be checked.
        out_paths
            A list of output paths to be checked.
        vol_paths
            A list of volatile output paths to be checked.
        """
        for inp_path in inp_paths:
            file_key = f"file:{inp_path}"
            if file_key in self.nodes:
                if (
                    not self.is_orphan(file_key)
                    and self.get_file(file_key).get_state(self) == FileState.VOLATILE
                ):
                    raise GraphError(f"Input is volatile: {inp_path}")
                self.check_cyclic(file_key, step_key)
        for out_path in out_paths:
            file_key = f"file:{out_path}"
            if file_key in self.nodes:
                if file_key in self.nodes and not self.is_orphan(file_key):
                    raise GraphError(f"Output is already created: {out_path}")
                self.check_cyclic(step_key, file_key)
        for vol_path in vol_paths:
            file_key = f"file:{vol_path}"
            if vol_path.endswith(os.sep):
                raise GraphError("A volatile output cannot be a directory.")
            if file_key in self.nodes:
                if file_key in self.nodes and not self.is_orphan(file_key):
                    raise GraphError(f"Volatile output is already created: {vol_path}")
                if len(self.consumers.get(file_key, ())) > 0:
                    raise GraphError(f"An input to an existing step cannot be volatile: {vol_path}")

    def define_step(
        self,
        creator_key: str,
        command: str,
        inp_paths: Collection[str] = (),
        env_vars: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
        workdir: str = "./",
        optional: bool = False,
        pool: str | None = None,
        block: bool = False,
    ) -> str:
        """Define a new step.

        Parameters
        ----------
        command
            A command that can be executed by /usr/bin/sh.
        inp_paths
            Input paths.
        env_vars
            The environment variables (possibly) used by the step.
        out_paths
            Output paths.
        vol_paths
            Volatile output (not reproducible) but will be cleaned like built files.
        workdir
            The directory where the command should be executed,
            typically relative to the working directory of the director.
        optional
            If True, the step is only executed when required by other mandatory steps.
        creator_key
            The step that generated this step.
            This is None for the boot script.
        pool
            The pool in which to execute this step, if any.
        block
            Block the step from being executed, convenient for temporarily reducing the workflow
            without cleaning up results of blocked steps.

        Returns
        -------
        step_key
            The key of the newly created step (or existing if it was previously stale).
        """
        self._check_step_key(creator_key, "creator_key", allow_root=True)

        # Prepare step for sanity check
        if not workdir.endswith(os.sep):
            raise GraphError("The working directory must end with a trailing separator")
        step = Step(command, workdir, pool=pool, initial_env_vars=set(env_vars), block=block)
        if step.key in self.nodes and not self.is_orphan(step.key):
            raise GraphError(f"Cannot define a step twice: {step.key}")

        # If it is a boot step, first remove the old one.
        if creator_key == "root:" and len(self.get_products("root:", kind="step")) > 0:
            raise GraphError(f"Boot step already defined: {step.key}")

        # Check arguments first, so errors are Minor errors can be raised before making changes.
        inp_paths = sorted(set(inp_paths))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        self.check_inputs_outputs(step.key, inp_paths, out_paths, vol_paths)

        # Create step
        self.graph_changed = True
        step_key = self.create(step, creator_key)
        step.set_state(self, StepState.PENDING)
        step.set_mandatory(self, Mandatory.NO if optional else Mandatory.YES)

        # Supply required directories
        reqdirs = {workdir}
        reqdirs.update(myparent(out_path) for out_path in out_paths)
        reqdirs.update(myparent(vol_path) for vol_path in vol_paths)
        reqdirs.difference_update(out_paths)
        reqdirs.difference_update(vol_paths)
        for reqdir in sorted(reqdirs):
            self.supply_file(step_key, reqdir, new=False)

        # Supply inp_paths
        for inp_path in inp_paths:
            # Input coinciding with reqdirs are ignored.
            if inp_path not in reqdirs:
                self.supply_file(step_key, inp_path)

        # Create out_paths
        for out_path in out_paths:
            self.create_file(step_key, out_path, FileState.PENDING)

        # Create vol_paths
        for vol_path in vol_paths:
            self.create_file(step_key, vol_path, FileState.VOLATILE)

        # Restore amend from previous successful execution if available.
        step.replay_amend(self)

        # Determine if the step needs executing and queue if relevant.
        if optional:
            step.infer_mandatory(self)
        else:
            step.imply_mandatory_suppliers(self)
            step.queue_if_appropriate(self)

        return step_key

    def amend_step(
        self,
        step_key: str,
        inp_paths: Collection[str] = (),
        env_vars: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
    ):
        """Define a new step.

        Parameters
        ----------
        inp_paths
            Additional input paths.
        env_vars
            Additional environment variables that the step is using.
        out_paths
            Additional output paths.
        vol_paths
            Volatile output (not reproducible) but will be cleaned like built files.
        step_key
            The step specifying the additional info.

        Returns
        -------
        keep_going
            True when inputs are readily available.
            False otherwise, meaning the step needs to be rescheduled.
        """
        self._check_step_key(step_key, "step_key")
        step = self.get_step(step_key)
        inp_paths = sorted(set(inp_paths))
        out_paths = sorted(set(out_paths))
        vol_paths = sorted(set(vol_paths))
        self.check_inputs_outputs(step_key, inp_paths, out_paths, vol_paths)
        missing = set()

        # Supply required directories
        reqdirs = set()
        reqdirs.update(myparent(out_path) for out_path in out_paths)
        reqdirs.update(myparent(vol_path) for vol_path in vol_paths)
        reqdirs.difference_update(out_paths)
        reqdirs.difference_update(vol_paths)
        reqdirs.difference_update(step.get_out_paths(self))
        reqdirs.difference_update(step.get_vol_paths(self))
        self.graph_changed = True
        for reqdir in sorted(reqdirs):
            file_key, available, new_relation = self.supply_file(step_key, reqdir, new=False)
            if not available:
                missing.add(reqdir)
            if new_relation:
                step.amended_suppliers.add(file_key)

        # Process inp_paths
        new_inp_path_keys = []
        for inp_path in inp_paths:
            file_key, available, new_relation = self.supply_file(step_key, inp_path, new=False)
            if not available:
                missing.add(inp_path)
            if new_relation:
                step.amended_suppliers.add(file_key)
                new_inp_path_keys.append(file_key)

        # Process vars
        env_vars = set(env_vars)
        env_vars.difference_update(step.initial_env_vars)
        step.amended_env_vars.update(env_vars)

        # Create out_paths
        for out_path in out_paths:
            file_key = self.create_file(step_key, out_path, FileState.PENDING)
            step.amended_consumers.add(file_key)

        # Create vol_paths
        for vol_path in vol_paths:
            file_key = self.create_file(step_key, vol_path, FileState.VOLATILE)
            step.amended_consumers.add(file_key)

        if len(inp_paths) > 0:
            step.imply_mandatory_suppliers(self, new_inp_path_keys)

        step.reschedule_due_to.update(missing)

        return len(missing) == 0

    def register_nglob(self, creator_key: str, ngm: NGlobMulti):
        self._check_step_key(creator_key, "creator_key")
        step = self.get_step(creator_key)
        self.graph_changed = True
        step.register_nglob(self, ngm)

    def defer_glob(self, creator_key: str, patterns: Collection[str]) -> str:
        self._check_step_key(creator_key, "creator_key")
        if isinstance(patterns, str):
            raise TypeError("The argument patterns cannot be a string.")
        for pattern in patterns:
            if pattern.startswith("/"):
                raise ValueError(f"Deferred glob patterns cannot be absolute paths: {pattern}")
        ngm = NGlobMulti.from_patterns(patterns)
        if not DeferredGlob.valid_ngm(ngm):
            raise GraphError("Named wildcards are not supported in deferred globs.")
        dg = DeferredGlob(ngm)
        if dg.key in self.nodes and not self.is_orphan(dg.key):
            raise GraphError(f"Cannot define a deferred nglob twice: {dg.key}")
        self.graph_changed = True
        self.create(dg, creator_key)
        # Check for matches in existing files
        ngm.extend([file.path for file in self.get_files(include_orphans=True)])
        self.declare_static(dg.key, ngm.files())
        return dg.key

    def set_file_hash(self, path: str, file_hash: FileHash):
        self.get_file(f"file:{path}").hash = file_hash

    def clean(self):
        for step_key in self.step_mandatory.inverse.get(Mandatory.NO, []):
            self.orphan(step_key)
        for dg in self.get_deferred_globs():
            file_keys = self.get_products(dg.key)
            orphaned_paths = []
            for file_key in file_keys[::-1]:
                if len(self.get_consumers(file_key)) == 0:
                    assert file_key.startswith("file:")
                    self.orphan(file_key)
                    orphaned_paths.append(file_key[5:])
            dg.ngm.reduce(orphaned_paths)
        super().clean()

    #
    # Watch phase
    #

    def is_relevant(self, path: str):
        file = self.get_file(f"file:{path}")
        if file is not None:
            return file.get_state(self) != FileState.VOLATILE
        for step_key in self.step_keys_with_nglob:
            step = self.get_step(step_key)
            if any(ngm.may_change(set(), {path}) for ngm in step.nglob_multis):
                return True
        return False

    def process_watcher_changes(self, deleted: Collection[str], updated: Collection[str]):
        """Update the workflow given a list of deleted and updated paths observed by the watcher.

        Parameters
        ----------
        deleted
            The deleted files.
        updated
            The added / changed files.
        """
        # Sanity check
        if not set(deleted).isdisjoint(updated):
            raise ValueError("Added and deleted files are not mutually exclusive.")

        # Process all deletions
        for path in deleted:
            # Handle deleted files that were used or created by steps.
            file = self.get_file(f"file:{path}")
            if file is None:
                raise ValueError("Cannot process deletion of file absent from workflow")
            file.watcher_deleted(self)

        # Process all updates
        for path in updated:
            file = self.get_file(f"file:{path}")
            # If updated the file is known, it must have changed (or a MISSING file was added).
            if file is not None:
                file.watcher_updated(self)

        for step_key in sorted(self.step_keys_with_nglob):
            step = self.get_step(step_key)
            # Check if any of the deleted files matches an nglob. If yes, step becomes pending.
            # Check if added files could result in new nglob matches. If yes, step becomes pending.
            if not self.is_orphan(step_key) and any(
                ngm.will_change(deleted, updated) for ngm in step.nglob_multis
            ):
                step.discard_recording()
                step.make_pending(self, input_changed=True)
            # In case the step gets skipped, we need to make sure the nglob results
            # are up to date.
            for ngm in step.nglob_multis:
                ngm.reduce(deleted)
                ngm.extend(updated)

        # Queue pending steps that can be executed.
        for step_key in sorted(self.step_states.inverse.get(StepState.PENDING, ())):
            self.get_step(step_key).queue_if_appropriate(self)
