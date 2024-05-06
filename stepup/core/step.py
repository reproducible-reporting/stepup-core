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
"""A `Step` is a shell command that can be executed and that has inputs and outputs."""

import enum
from collections.abc import Collection, Iterator
from typing import TYPE_CHECKING, Any, Self

import attrs

from .cascade import Node
from .file import FileState
from .hash import ExtendedStepHash, StepHash
from .job import RunJob, TryReplayJob, ValidateAmendedJob
from .nglob import NGlobMulti
from .utils import format_digest

if TYPE_CHECKING:
    from .workflow import Workflow


__all__ = ("Mandatory", "StepState", "StepRecording", "Step")


class StepState(enum.Enum):
    PENDING = 21
    QUEUED = 22
    RUNNING = 23
    SUCCEEDED = 24
    FAILED = 25


class Mandatory(enum.Enum):
    YES = 31
    IMPLIED = 32
    NO = 33


@attrs.define
class StepRecording:
    """All info to repeat the consequences of a step without actually executing it."""

    # Needed for checking the recording validity
    key: str = attrs.field()
    initial_inp_paths: list[str] = attrs.field(factory=list)
    initial_env_vars: set[str] = attrs.field(factory=set)
    initial_out_paths: list[str] = attrs.field(factory=list)
    initial_vol_paths: list[str] = attrs.field(factory=list)
    # Amended while running the step
    amend_args: dict = attrs.field(factory=dict)
    # Created while running the step
    static_paths: list[str] = attrs.field(factory=list)
    steps_args: list[dict] = attrs.field(factory=list)
    nglob_multis: list[NGlobMulti] = attrs.field(factory=list)
    deferred_glob_args: list[list[str]] = attrs.field(factory=list)


@attrs.define
class Step(Node):
    _command: str = attrs.field()
    _workdir: str = attrs.field()
    _pool: str | None = attrs.field(kw_only=True, default=None)
    # When True, the step will behave as it never has all dependencies satisfied.
    # This is convenient for lowering the build time when working on intermediate steps.
    _block: bool = attrs.field(kw_only=True, default=False)

    # Augment information in Workflow instance
    _amended_suppliers: set[str] = attrs.field(kw_only=True, factory=set)
    _amended_consumers: set[str] = attrs.field(kw_only=True, factory=set)

    # Extra information, not in Workflow instance
    _initial_env_vars: set[str] = attrs.field(kw_only=True, factory=set)
    _amended_env_vars: set[str] = attrs.field(kw_only=True, factory=set)
    _nglob_multis: list[NGlobMulti] = attrs.field(kw_only=True, factory=list)

    # List of missing amended files causing reschedule
    reschedule_due_to: set[str] = attrs.field(init=False, factory=set)
    # Flow to validate the amended inputs, e.g. because inputs may have changed.
    validate_amended: bool = attrs.field(init=False, default=True)

    # Attributes for skipping steps whose inputs and outputs have not changed on disk.
    _hash: StepHash | None = attrs.field(kw_only=True, default=None)
    _recording: StepRecording | None = attrs.field(kw_only=True, default=None)

    #
    # Getters
    #

    @property
    def workdir(self) -> str:
        return self._workdir

    @property
    def command(self) -> str:
        return self._command

    @property
    def pool(self) -> str | None:
        return self._pool

    @property
    def block(self) -> bool:
        return self._block

    @property
    def initial_env_vars(self) -> set[str]:
        return self._initial_env_vars

    @property
    def amended_suppliers(self) -> set[str]:
        return self._amended_suppliers

    @property
    def amended_consumers(self) -> set[str]:
        return self._amended_consumers

    @property
    def amended_env_vars(self) -> set[str]:
        return self._amended_env_vars

    @property
    def nglob_multis(self) -> list[NGlobMulti]:
        return self._nglob_multis

    @property
    def hash(self) -> StepHash:
        return self._hash

    @property
    def recording(self) -> StepRecording:
        return self._recording

    #
    # Initialization, serialization and formatting
    #

    @classmethod
    def key_tail(cls, data: dict[str, Any], strings: list[str] | None = None) -> str:
        """Subclasses must implement the key tail and accept both JSON or attrs dicts."""
        result = data.get("_command", data.get("m"))
        workdir = data.get("_workdir")
        if workdir is None:
            workdir = strings[data.get("w")]
        if workdir != "./":
            result += f"  # wd={workdir}"
        return result

    @classmethod
    def structure(cls, workflow: "Workflow", strings: list[str], data: dict) -> Self:
        state = StepState(data.pop("s"))
        mandatory = Mandatory(data.pop("t", Mandatory.YES))
        kwargs = {
            "workdir": strings[data["w"]],
            "command": data["m"],
        }
        pool = data.get("p")
        if pool is not None:
            kwargs["pool"] = pool
        kwargs["block"] = data.get("b", False)
        initial_env_vars = data.get("i")
        if initial_env_vars is not None:
            kwargs["initial_env_vars"] = set(initial_env_vars)
        for short, name in ("as", "amended_suppliers"), ("ac", "amended_consumers"):
            idxs = data.get(short)
            if idxs is not None:
                kwargs[name] = {strings[idx] for idx in idxs}
        amended_env_vars = data.get("ai")
        if amended_env_vars is not None:
            kwargs["amended_env_vars"] = set(amended_env_vars)
        ngm_datas = data.get("g")
        if ngm_datas is not None:
            kwargs["nglob_multis"] = [
                NGlobMulti.structure(ngm_data, strings) for ngm_data in ngm_datas
            ]
        hash_ = data.get("h")
        if hash_ is not None:
            kwargs["hash"] = StepHash.structure(hash_, strings)
        step = cls(**kwargs)
        workflow.step_states[step.key] = state
        workflow.step_mandatory[step.key] = mandatory
        if ngm_datas is not None:
            workflow.step_keys_with_nglob.add(step.key)
        return step

    def unstructure(self, workflow: "Workflow", lookup: dict[str, int]) -> dict:
        data = {
            "w": lookup[self._workdir],
            "m": self._command,
            "s": self.get_state(workflow).value,
        }
        mandatory = self.get_mandatory(workflow)
        if mandatory != Mandatory.YES:
            data["t"] = mandatory.value
        if self._pool is not None:
            data["p"] = self._pool
        if self._block:
            data["b"] = True
        if len(self._initial_env_vars) > 0:
            data["i"] = sorted(self._initial_env_vars)
        if len(self._amended_suppliers) > 0:
            data["as"] = sorted(lookup[key] for key in self._amended_suppliers)
        if len(self._amended_consumers) > 0:
            data["ac"] = sorted(lookup[key] for key in self._amended_consumers)
        if len(self._amended_env_vars) > 0:
            data["ai"] = sorted(self._amended_env_vars)
        if len(self._nglob_multis) > 0:
            data["g"] = [nglob_multi.unstructure(lookup) for nglob_multi in self._nglob_multis]
        if self._hash is not None:
            data["h"] = self._hash.unstructure(lookup)
        return data

    def format_properties(self, workflow: "Workflow") -> Iterator[tuple[str, str]]:
        yield "workdir", self._workdir
        yield "command", self._command
        yield "state", str(self.get_state(workflow).name)
        mandatory = self.get_mandatory(workflow)
        if mandatory != Mandatory.YES:
            yield "mandatory", mandatory.name
        if self._pool is not None:
            yield "pool", self._pool
        if self._block:
            yield "block", self._block
        label = "env_var"
        for env_var in sorted(self._initial_env_vars):
            yield label, env_var
            label = ""
        label = "consumes (amended)"
        for supplier in sorted(self._amended_suppliers):
            yield label, supplier
            label = ""
        label = "supplies (amended)"
        for product in sorted(self._amended_consumers):
            yield label, product
            label = ""
        label = "env_var (amended)"
        for env_var in sorted(self._amended_env_vars):
            yield label, env_var
            label = ""
        for ngm in self._nglob_multis:
            yield "ngm", f"{[ngs.pattern for ngs in ngm.nglob_singles]} {ngm.subs}"
        if self._hash is not None:
            l1, l2 = format_digest(self._hash.digest)
            yield "digest", l1
            yield "", l2
            l1, l2 = format_digest(self._hash.inp_digest)
            yield "inp_digest", l1
            yield "", l2
            if isinstance(self._hash, ExtendedStepHash):
                yield "extended hash", "yes"

    #
    # Overridden from base class
    #

    def recycle(self, workflow: "Workflow", old: Self | None):
        if old is not None:
            self._hash = old._hash
            self._recording = old._recording

    def orphan(self, workflow: "Workflow"):
        if self.get_mandatory(workflow) != Mandatory.NO:
            self.undo_mandatory_suppliers(workflow)

    def cleanup(self, workflow: "Workflow"):
        workflow.step_states.discard(self.key, insist=True)
        workflow.step_keys_with_nglob.discard(self.key)
        workflow.step_mandatory.discard(self.key)
        self._nglob_multis.clear()

    #
    # Mandatory / Optional
    #

    def get_mandatory(self, workflow: "Workflow"):
        return workflow.step_mandatory[self.key]

    def set_mandatory(self, workflow: "Workflow", mandatory: Mandatory):
        workflow.step_mandatory[self.key] = mandatory

    def infer_mandatory(self, workflow: "Workflow") -> bool:
        """Decide if Mandatory.NO should become Mandatory.IMPLIED.

        Returns
        -------
        implied_or_yes
            True when the step must is mandatory or (has become) implied.
        """
        if self.get_mandatory(workflow) != Mandatory.NO:
            return True
        # Get a list of steps using the outputs of this step.
        step_keys = set()
        for file_key in workflow.get_products(self.key, kind="file"):
            step_keys.update(workflow.get_consumers(file_key, kind="step"))
        implied = False
        for step_key in step_keys:
            step = workflow.get_step(step_key)
            if step.infer_mandatory(workflow):
                implied = True
        if implied:
            self.imply_mandatory(workflow)
        return implied

    def imply_mandatory(self, workflow: "Workflow"):
        """Make this step Mandatory.IMPLIED and propagate this to its suppliers."""
        assert self.get_mandatory(workflow) == Mandatory.NO
        self.set_mandatory(workflow, Mandatory.IMPLIED)
        self.imply_mandatory_suppliers(workflow)
        self.queue_if_appropriate(workflow)

    def imply_mandatory_suppliers(
        self, workflow: "Workflow", file_keys: Collection[str] | None = None
    ):
        """Make supplying steps Mandatory.IMPLIED (if they were Mandatory.NO)."""
        assert self.get_mandatory(workflow) != Mandatory.NO
        if file_keys is None:
            file_keys = workflow.get_suppliers(self.key, kind="file")
        step_keys = set()
        for file_key in file_keys:
            creator_key = workflow.get_creator(file_key)
            if creator_key.startswith("step:"):
                step_keys.add(creator_key)
        for step_key in step_keys:
            step = workflow.get_step(step_key)
            if step.get_mandatory(workflow) == Mandatory.NO:
                step.imply_mandatory(workflow)

    def undo_mandatory(self, workflow: "Workflow"):
        assert self.get_mandatory(workflow) == Mandatory.IMPLIED
        self.undo_mandatory_suppliers(workflow)
        self.set_mandatory(workflow, Mandatory.NO)

    def undo_mandatory_suppliers(self, workflow: "Workflow"):
        """Make supplying steps Mandatory.NO (if they were Mandatory.IMPLIED)."""
        assert self.get_mandatory(workflow) != Mandatory.NO
        step_keys = set()
        for file_key in workflow.get_suppliers(self.key, kind="file"):
            creator_key = workflow.get_creator(file_key)
            if creator_key.startswith("step:"):
                step_keys.add(creator_key)
        for step_key in step_keys:
            step = workflow.get_step(step_key)
            if step.get_mandatory(workflow) == Mandatory.IMPLIED:
                step.undo_mandatory(workflow)

    #
    # Path getters
    #

    @staticmethod
    def _get_paths(
        workflow: "Workflow",
        file_keys: list[str],
        state=False,
        file_hash=False,
        orphan=False,
        filter_states: tuple[FileState, ...] | None = None,
    ) -> list:
        result = []
        for file_key in file_keys:
            file = workflow.get_file(file_key)
            if filter_states is None or file.get_state(workflow) in filter_states:
                row = [file.path]
                if state:
                    row.append(file.get_state(workflow))
                if file_hash:
                    row.append(file.hash)
                if orphan:
                    row.append(workflow.is_orphan(file_key))
                result.append(row)
        if not (state or file_hash or orphan):
            result = [row[0] for row in result]
        return result

    def get_inp_paths(
        self,
        workflow: "Workflow",
        *,
        state=False,
        file_hash=False,
        orphan=False,
        only_initial=False,
    ) -> list:
        file_keys = workflow.get_suppliers(self.key, kind="file", include_orphans=True)
        if only_initial:
            file_keys = [fk for fk in file_keys if fk not in self._amended_suppliers]
        return self._get_paths(workflow, file_keys, state, file_hash, orphan)

    def get_out_paths(
        self,
        workflow: "Workflow",
        *,
        state=False,
        file_hash=False,
        only_initial=False,
    ) -> list:
        file_keys = workflow.get_consumers(self.key, kind="file")
        if only_initial:
            file_keys = [fk for fk in file_keys if fk not in self._amended_consumers]
        filter_states = (FileState.PENDING, FileState.BUILT)
        return self._get_paths(workflow, file_keys, state, file_hash, False, filter_states)

    def get_vol_paths(
        self,
        workflow: "Workflow",
        *,
        file_hash=False,
        only_initial=False,
    ) -> list:
        file_keys = workflow.get_consumers(self.key, kind="file")
        if only_initial:
            file_keys = [fk for fk in file_keys if fk not in self._amended_consumers]
        filter_states = (FileState.VOLATILE,)
        return self._get_paths(workflow, file_keys, False, file_hash, False, filter_states)

    def get_static_paths(self, workflow: "Workflow", *, state=False, file_hash=False) -> list:
        file_keys = workflow.get_products(self.key, kind="file")
        filter_states = (FileState.STATIC, FileState.MISSING)
        return self._get_paths(workflow, file_keys, state, file_hash, False, filter_states)

    #
    # Step state
    #

    def get_state(self, workflow: "Workflow") -> StepState:
        return workflow.step_states[self.key]

    def set_state(self, workflow: "Workflow", new_state: StepState):
        workflow.step_states[self.key] = new_state

    #
    # Run phase
    #

    def queue_if_appropriate(self, workflow: "Workflow"):
        if self._block:
            return
        if self.get_mandatory(workflow) == Mandatory.NO:
            return
        if workflow.is_orphan(self.key):
            return
        if self.get_state(workflow) != StepState.PENDING:
            return
        has_amended_pending = any(
            workflow.get_file(file_key).get_state(workflow)
            not in (FileState.STATIC, FileState.BUILT)
            for file_key in self._amended_suppliers
        )
        if has_amended_pending and self.validate_amended:
            job = ValidateAmendedJob(self._key, self._pool)
        else:
            for file_key in workflow.get_suppliers(self.key, kind="file", include_orphans=True):
                if workflow.is_orphan(file_key):
                    return
                state = workflow.get_file(file_key).get_state(workflow)
                if state not in (FileState.BUILT, FileState.STATIC):
                    return
            if self._hash is None or self._recording is None:
                job = RunJob(self._key, self._pool)
            else:
                job = TryReplayJob(self._key, self._pool)
        self.set_state(workflow, StepState.QUEUED)
        self.reschedule_due_to = set()
        self.validate_amended = False
        workflow.job_queue.put_nowait(job)
        workflow.job_queue_changed.set()

    def clean_before_run(self, workflow: "Workflow"):
        """Drop amended inputs and (volatile) outputs.

        This method is called right before (re)running a step.
        Running the step will effectively recreate
        the same or different amended inputs and (volatile) outputs.
        """
        for supplier_key in self._amended_suppliers:
            workflow.consumers.discard(supplier_key, self._key, insist=True)
        self._amended_suppliers.clear()
        for consumer_key in sorted(self._amended_consumers):
            consumer = workflow.get_file(consumer_key)
            assert consumer.get_state(workflow) in (FileState.PENDING, FileState.VOLATILE)
            workflow.suppliers.discard(consumer_key, self.key, insist=True)
            workflow.orphan(consumer_key)
        self._amended_consumers.clear()
        self._amended_env_vars.clear()
        self._nglob_multis = []

    def completed(self, workflow: "Workflow", success: bool, new_hash: StepHash | None) -> set[str]:
        """Set a step as completed (succeeded or failed) and trigger the consequences.

        Parameters
        ----------
        workflow
            The workflow in which to trigger the consequences.
        success
            True if the step completed without error, False otherwise.
        new_hash
            The new digest of the completed step.
            Required if the step was successful or rescheduled.
            Allowed to be None when the step failed.

        Returns
        -------
        reschedule_due_to
            A set of missing files that caused the step to be rescheduled, if any.
        """
        self._hash = new_hash
        if len(self.reschedule_due_to) > 0:
            assert new_hash is not None
            self.set_state(workflow, StepState.PENDING)
            # The missing inputs may have appeared by the time the step ended,
            # so we need to check if we can put the step back on the queue right away.
            missing = self.reschedule_due_to.copy()
            self.queue_if_appropriate(workflow)
            return missing
        if success:
            assert new_hash is not None
            self.set_state(workflow, StepState.SUCCEEDED)
            for file_key in workflow.get_products(self._key, kind="file"):
                file = workflow.get_file(file_key)
                if file.get_state(workflow) == FileState.PENDING:
                    file.set_state(workflow, FileState.BUILT)
                file.release_pending(workflow)
            self._hash = new_hash
            self.update_recording(workflow)
        else:
            self.discard_recording()
            self.set_state(workflow, StepState.FAILED)
        return []

    def update_recording(self, workflow: "Workflow"):
        """Derive the recording from the (fully intact) step in its workflow."""
        recording = StepRecording(self._key)

        for inp_path in self.get_inp_paths(workflow):
            file_key = f"file:{inp_path}"
            if file_key in self.amended_suppliers:
                recording.amend_args.setdefault("inp_paths", []).append(inp_path)
            else:
                recording.initial_inp_paths.append(inp_path)

        recording.initial_env_vars = set(self._initial_env_vars)
        recording.amend_args["env_vars"] = set(self._amended_env_vars)

        for out_path in self.get_out_paths(workflow):
            file_key = f"file:{out_path}"
            if file_key in self.amended_consumers:
                recording.amend_args.setdefault("out_paths", []).append(out_path)
            else:
                recording.initial_out_paths.append(out_path)

        for vol_path in self.get_vol_paths(workflow):
            file_key = f"file:{vol_path}"
            if file_key in self.amended_consumers:
                recording.amend_args.setdefault("vol_paths", []).append(vol_path)
            else:
                recording.initial_vol_paths.append(vol_path)

        if len(recording.amend_args) > 0:
            recording.amend_args["step_key"] = self.key
            recording.amend_args.setdefault("inp_paths", [])
            recording.amend_args.setdefault("out_paths", [])
            recording.amend_args.setdefault("vol_paths", [])

        recording.static_paths.extend(self.get_static_paths(workflow))
        for sub_step_key in workflow.get_products(self.key, kind="step"):
            sub_step = workflow.get_step(sub_step_key)
            step_args = {
                "creator_key": self.key,
                "command": sub_step.command,
                "inp_paths": sub_step.get_inp_paths(workflow, only_initial=True),
                "env_vars": sub_step.initial_env_vars.copy(),
                "out_paths": sub_step.get_out_paths(workflow, only_initial=True),
                "vol_paths": sub_step.get_vol_paths(workflow, only_initial=True),
                "workdir": sub_step.workdir,
                "optional": sub_step.get_mandatory(workflow) != Mandatory.YES,
                "pool": sub_step.pool,
            }
            recording.steps_args.append(step_args)
        for ngm in self.nglob_multis:
            recording.nglob_multis.append(ngm)
        for dg_key in workflow.get_products(self.key, "dg"):
            dg = workflow.get_deferred_glob(dg_key)
            patterns = [ngs.pattern for ngs in dg.ngm.nglob_singles]
            recording.deferred_glob_args.append(patterns)
        self._recording = recording

    def replay_amend(self, workflow: "Workflow"):
        """Restore amended paths from a previous execution.

        This should be done as early as possible, to keep the rest of the workflow informed.
        """
        # Don't bother if there is no recording.
        if self._recording is None:
            return
        if self._recording.key != self.key:
            raise ValueError("The recorded key is not consistent with the step key")
        if (
            len(self._amended_consumers) != 0
            or len(self._amended_suppliers) != 0
            or len(self._amended_env_vars) != 0
        ):
            raise ValueError("Cannot restore amended info if step is already amended")
        if (
            self._recording.initial_inp_paths == self.get_inp_paths(workflow, only_initial=True)
            and self._recording.initial_env_vars == self.initial_env_vars
            and self._recording.initial_out_paths == self.get_out_paths(workflow, only_initial=True)
            and self._recording.initial_vol_paths == self.get_vol_paths(workflow, only_initial=True)
        ):
            if len(self._recording.amend_args) > 0:
                workflow.amend_step(**self._recording.amend_args)
        else:
            self.discard_recording()

    def discard_recording(self):
        self._hash = None
        self._recording = None

    def replay_rest(self, workflow: "Workflow"):
        """Restore all consequences of step execution, other than amend.

        This is suitable when inputs and outputs (file contents) have not changed.
        """
        if self._recording is None:
            raise ValueError("The recording is not defined")
        if self._recording.key != self.key:
            raise ValueError("The recorded key is not consistent with the step key")
        for file_key in workflow.get_products(self.key, kind="file"):
            file = workflow.get_file(file_key)
            if file.get_state(workflow) in (FileState.MISSING, FileState.BUILT):
                raise ValueError("Upon replay, output files cannot be MISSING or BUILT")
        recording = self._recording
        # Restore as if the step executed
        workflow.declare_static(self.key, recording.static_paths)
        for step_args in recording.steps_args:
            workflow.define_step(**step_args)
        for ngm in recording.nglob_multis:
            workflow.register_nglob(self.key, ngm)
        for patterns in recording.deferred_glob_args:
            workflow.defer_glob(self.key, patterns)
        # Mark the step as succeeded and mark outputs as BUILT
        self.set_state(workflow, StepState.SUCCEEDED)
        for file_key in workflow.get_consumers(self.key, kind="file"):
            file = workflow.get_file(file_key)
            if file.get_state(workflow) == FileState.PENDING:
                file.set_state(workflow, FileState.BUILT)
            file.release_pending(workflow)

    def register_nglob(self, workflow, nglob_multi):
        self.nglob_multis.append(nglob_multi)
        workflow.step_keys_with_nglob.add(self.key)

    #
    # Watch phase
    #

    def make_pending(self, workflow: "Workflow", *, input_changed: bool = False):
        self.validate_amended |= input_changed
        if self.get_state(workflow) != StepState.PENDING:
            self.set_state(workflow, StepState.PENDING)
            # First make all consumers (output files) pending
            for key in workflow.get_consumers(self.key):
                if key.startswith("file:"):
                    file = workflow.get_file(key)
                    if file.get_state(workflow) == FileState.BUILT:
                        file.make_pending(workflow)
            # Then orphan all products that are not (volatile) output files
            for key in workflow.get_products(self.key):
                if key.startswith("file:"):
                    file = workflow.get_file(key)
                    if file.get_state(workflow) in (
                        FileState.BUILT,
                        FileState.PENDING,
                        FileState.VOLATILE,
                    ):
                        continue
                workflow.orphan(key)
