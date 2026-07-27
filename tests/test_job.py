# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for `stepup.core.job`."""

from types import SimpleNamespace

import pytest

from stepup.core.job import Job, RunJob, ValidateAmendedJob


def _make_job(cls, step_hash):
    return cls(SimpleNamespace(label="echo hi"), {}, [], step_hash, job_i=1)


@pytest.mark.parametrize(
    ("cls", "step_hash", "prefix", "letter"),
    [
        (RunJob, None, "RUN", "R"),
        (RunJob, "fake-hash", "SKIP", "S"),
        (ValidateAmendedJob, "fake-hash", "VALIDATE_AMENDED", "V"),
    ],
)
def test_prefix_letter_and_name(cls, step_hash, prefix, letter):
    """`letter` is the first character of `prefix`, while `name` spells the prefix out."""
    job = _make_job(cls, step_hash)
    assert job.prefix == prefix
    assert job.letter == letter
    assert job.label == "echo hi"
    assert job.name == f"{prefix}: echo hi"


def test_base_job_prefix_is_abstract():
    """`Job` itself has no prefix, so `letter` cannot be derived either."""
    job = _make_job(Job, None)
    with pytest.raises(NotImplementedError):
        _ = job.prefix
    with pytest.raises(NotImplementedError):
        _ = job.letter
