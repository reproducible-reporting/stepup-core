# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.status."""

import pytest

from stepup.core.constants import GRAPH_DB
from stepup.core.enums import FileState, Need, StepState
from stepup.core.file import FILE_SCHEMA
from stepup.core.sqlite3 import connect
from stepup.core.status import print_status, status_tool
from stepup.core.step import STEP_SCHEMA
from stepup.core.trellis import TRELLIS_SCHEMA


@pytest.fixture
def con():
    """In-memory SQLite connection with the trellis + step + file schemas and a root node."""
    c = connect(":memory:")
    c.executescript(TRELLIS_SCHEMA.format(application_id=0, schema_version=0))
    c.executescript(STEP_SCHEMA)
    c.executescript(FILE_SCHEMA)
    c.execute("INSERT INTO node (i, kind, label, creator, detached) VALUES (1, 'root', '', 1, 0)")
    return c


def _insert_step(con, node_id, label, state, *, detached=False):
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'step', ?, 1, ?)",
        (node_id, label, detached),
    )
    con.execute(
        "INSERT INTO step"
        " (node, state, need, subshell, _safe, _check_safe, _implied_need, _check_after)"
        " VALUES (?, ?, ?, 0, 0, 0, ?, 0)",
        (node_id, state.value, Need.DEFAULT.value, Need.DEFAULT.value),
    )


def _insert_file(con, node_id, label, state, *, detached=False):
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'file', ?, 1, ?)",
        (node_id, label, detached),
    )
    # STATIC/BUILT/OUTDATED files require a non-null (JSON) hash.
    needs_hash = state in (FileState.STATIC, FileState.BUILT, FileState.OUTDATED)
    con.execute(
        "INSERT INTO file (node, state, hash) VALUES (?, ?, ?)",
        (node_id, state.value, "{}" if needs_hash else None),
    )


def test_print_status_empty(con, capsys):
    print_status(con)
    out = capsys.readouterr().out
    assert "Step counts" in out
    assert "File counts" in out
    assert "Resources" in out
    assert "Running steps" in out


def test_print_status_step_counts(con, capsys):
    _insert_step(con, 2, "echo one", StepState.PENDING)
    _insert_step(con, 3, "echo two", StepState.SUCCEEDED)
    _insert_step(con, 4, "echo three", StepState.SUCCEEDED)
    print_status(con)
    out = capsys.readouterr().out
    assert "PENDING         1" in out
    assert "SUCCEEDED       2" in out


def test_print_status_ignores_detached(con, capsys):
    _insert_step(con, 2, "echo one", StepState.PENDING, detached=True)
    print_status(con)
    out = capsys.readouterr().out
    assert "PENDING" not in out


def test_print_status_file_counts(con, capsys):
    _insert_file(con, 2, "foo.txt", FileState.BUILT)
    _insert_file(con, 3, "bar.txt", FileState.MISSING)
    print_status(con)
    out = capsys.readouterr().out
    assert "BUILT           1" in out
    assert "MISSING         1" in out


def test_print_status_running_steps_order(con, capsys):
    _insert_step(con, 2, "echo checking", StepState.CHECKING)
    _insert_step(con, 3, "echo running", StepState.RUNNING)
    print_status(con)
    out = capsys.readouterr().out
    running_section = out.split("Running steps")[1]
    # RUNNING steps are listed before CHECKING steps.
    assert running_section.index("echo running") < running_section.index("echo checking")


def test_print_status_resource_used_only(con, capsys):
    _insert_step(con, 2, "echo gpu-job", StepState.RUNNING)
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 2)")
    print_status(con)
    out = capsys.readouterr().out
    assert "gpu  used      2" in out
    assert "available" not in out
    assert "/" not in out.split("Resources")[1].split("Running steps")[0]


def test_status_tool_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STEPUP_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="does not exist"):
        status_tool(None)


def test_status_tool_reads_graph_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STEPUP_ROOT", str(tmp_path))
    path_db = tmp_path / GRAPH_DB
    path_db.parent.mkdir(parents=True)
    c = connect(path_db)
    c.executescript(TRELLIS_SCHEMA.format(application_id=0, schema_version=0))
    c.executescript(STEP_SCHEMA)
    c.executescript(FILE_SCHEMA)
    c.execute("INSERT INTO node (i, kind, label, creator, detached) VALUES (1, 'root', '', 1, 0)")
    _insert_step(c, 2, "echo one", StepState.SUCCEEDED)
    c.commit()
    c.close()

    status_tool(None)
    out = capsys.readouterr().out
    assert "SUCCEEDED       1" in out
