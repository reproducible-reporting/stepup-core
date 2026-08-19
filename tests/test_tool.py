# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.tool."""

import sqlite3

import pytest
from path import Path

from stepup.core.constants import GRAPH_DB
from stepup.core.exceptions import ToolError
from stepup.core.sqlite3 import connect
from stepup.core.tool import connect_graph_db, get_graph_db_path, print_error


def _create_graph_db(path_tmp: Path) -> Path:
    """Create an empty graph database with one table to read from."""
    path_db = path_tmp / GRAPH_DB
    path_db.parent.makedirs_p()
    con = connect(path_db)
    con.execute("CREATE TABLE demo (i INTEGER PRIMARY KEY)")
    con.close()
    return path_db


def test_get_graph_db_path_missing(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory where StepUp has not run yet is a user mistake, not a bug."""
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    with pytest.raises(ToolError, match="does not exist"):
        get_graph_db_path()


def test_get_graph_db_path_relative_to_root(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The database is looked up under `STEPUP_ROOT`, not under the working directory."""
    path_db = _create_graph_db(path_tmp)
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    assert get_graph_db_path() == path_db


def test_connect_graph_db_is_read_only(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tools only inspect the workflow, so they may not write to a director's database."""
    _create_graph_db(path_tmp)
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    con = connect_graph_db()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("INSERT INTO demo (i) VALUES (1)")
    finally:
        con.close()


def test_print_error_goes_to_stderr(capsys: pytest.CaptureFixture) -> None:
    """An error message may never pollute the standard output of a subcommand."""
    print_error("something is wrong")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "ERROR: something is wrong"
