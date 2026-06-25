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
"""Web browser interface to StepUp's build graph."""

import argparse
import contextlib
import html
import importlib.resources
import json
import os
import pickle
import stat
import traceback
import webbrowser
from collections.abc import Callable, Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jinja2
from path import Path

from .config import ConfigLoader
from .constants import GRAPH_DB
from .enums import FileState, Need, StepState
from .hash import fmt_digest
from .sqlite3 import connect
from .step import Step
from .utils import format_subprocess


def browse_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    """Define command-line arguments for the browse tool.

    Parameters
    ----------
    subparsers
        The sub parser to add the browse tool to.
    loader
        The configuration loader to override the default configuration with
        config file values.
    """
    parser = subparsers.add_parser("browse", help="Browse the StepUp build graph.")
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the server to (default: 8000).",
    )
    loader.patch_parser(parser, "browse")
    return browse_tool


def browse_tool(args: argparse.Namespace):
    """Launch a web server to browse the build graph and print the URL to the console."""
    # Copy the database in memory and work on the copy.
    root = Path(os.getenv("STEPUP_ROOT", "."))
    path_db = root / GRAPH_DB
    if not path_db.exists():
        raise FileNotFoundError(f"Graph database {path_db} does not exist.")
    # Ugly hack to pass information to the request handler.
    GraphServer.path_db = path_db
    # Standard library HTTP server.
    server = HTTPServer(("localhost", args.port), GraphServer)
    url = f"http://localhost:{args.port}"
    print(f"Server started {url}")
    print("Press Ctrl+C to stop the server.")
    if not webbrowser.open(url):
        print("Warning: could not open a browser. Open the URL above manually.")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    server.server_close()


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <link rel="icon" href="/logo.svg">
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>StepUp Graph Browser</title>
  <style>
    :root {
      --main-font: IBM Plex Sans, Arial, sans-serif;
      --background-color: #eeeeee;
      --pre-color: #dddddd;
      --heading-color: #222222;
      --text-color: #000000;
      --link-color: #444444;
      --blue: #0077cc;
      --green: #009900;
      --red: #cc0000;
      --orange: #dd7700;
      --purple: #aa00dd;
      --yellow: #bbaa00;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --background-color: #181818;
        --pre-color: #333333;
        --heading-color: #cccccc;
        --text-color: #ffffff;
        --link-color: #aaaaaa;
        --blue: #0099ff;
        --green: #00cc00;
        --red: #ff0000;
        --orange: #ff8800;
        --purple: #dd00ff;
        --yellow: #ffdd00;
      }
    }
    body {
      font-family: var(--main-font);
      margin: 0;
      padding: 15px;
      color: var(--text-color);
      background-color: var(--background-color);
    }
    h1, h2, h3 {
      color: var(--heading-color);
      margin-bottom: 0px;
      margin-top: 8px;
    }
    hr {
      border: 1px solid var(--heading-color);
    }
    p {
      margin-left: 10px;
      margin-top: 5px;
      margin-bottom: 5px;
    }
    pre {
      background-color: var(--pre-color);
      margin-left: 10px;
      margin-top: 5px;
      margin-bottom: 5px;
      padding: 10px;
      border-radius: 4px;
    }
    a {
      color: var(--link-color);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    a#reload {
      font-size: 1em;
      border-radius: 4px;
      margin: 5px;
      padding: 3px 8px 3px 8px;
      color: var(--background-color);
      border: 0px;
      background-color: var(--link-color);
    }
    header {
    }
    main {
    }
    footer {
    }
    .alert {
      color: red;
      font-weight: bold;
    }
    input {
      font-family: var(--main-font);
      font-size: 1em;
      border-radius: 4px;
      margin: 5px;
      padding: 10px;
      cursor: pointer;
    }
    input[type=text] {
      box-sizing: border-box;
      color: var(--text-color);
      border: var(--heading-color) solid 1px;
      background-color: var(--background-color);
    }
    input[type=submit] {
      padding: 10px 20px 10px 20px;
      color: var(--background-color);
      border: 0px;
      background-color: var(--link-color);
    }
    .missing { color: var(--red); }
    .static { color: var(--blue); }
    .awaited { color: var(--orange); }
    .built { color: var(--green); }
    .outdated { color: var(--yellow); }
    .volatile { color: var(--purple); }
    .pending { color: var(--orange); }
    .queued { color: var(--yellow); }
    .running { color: var(--blue); }
    .succeeded { color: var(--green); }
    .failed { color: var(--red); }
    .yes { color: var(--green); }
    .no { color: var(--red); }
    .rescheduled { color: var(--orange); }
    .clean { color: var(--green); }
    .required { color: var(--blue); }
    table.list tr td {
      vertical-align: top;
      padding-top: 2px;
      padding-bottom: 2px;
    }
    td:nth-child(1) {
      text-align: right;
      padding-right: 10px;
      width: 12ex;
    }
    td:nth-child(2) {
      padding-right: 10px;
      padding-left: 10px;
    }
    tr {
      margin: 4px;
    }
  </style>
</head>
<body>
  <header>
    <a href="/"><h1>StepUp Graph Browser</h1></a>
    <p>{{ path_db }} <a id="reload" href="{{ reload_url }}">↻</a></p>
    <hr>
  </header>
  <main>
    {{ main }}
  </main>
</body>
</html>"""


MAIN_TEMPLATE = """\
<h2>Overview</h2>
<p>The graph contains <a href="/search_file/">{{ n_files }} files</a>
and <a href="/search_step/">{{ n_steps }} steps</a>.</p>
<p>Entry point: {{ a_entry }}<p>
<h2>Search</h2>
<p><form action="/search/" method="get">
  <table>
    <tr>
      <td><label for="pattern"><b>Pattern:</b></label></td>
      <td><input type="text" id="pattern" name="pattern" style="width: 500px;"></td>
    </tr>
    <tr>
      <td></td>
      <td><input type="submit" value="(Any)">
      <input type="submit" value="🗏 File" formaction="/search_file/">
      <input type="submit" value="⚙ Step" formaction="/search_step/"></td>
    </tr>
  </table>
</form></p>
"""

KIND_SYMBOLS = {
    "root": "⌂",
    "file": "🗏",
    "step": "⚙",
    "dg": "🟄",
}

KIND_NAMES = {
    "root": "Root",
    "file": "File",
    "step": "Step",
    "st": "Static Tree",
}

STATE_SQL = """
(CASE node.kind
  WHEN 'file' THEN (SELECT state FROM file WHERE file.node = node.i)
  WHEN 'step' THEN (SELECT state FROM step WHERE step.node = node.i)
  ELSE NULL
END)
"""


class GraphServer(BaseHTTPRequestHandler):
    path_db = None
    con = None

    def do_GET(self):
        # Basic URL parsing.
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)

        # (Re)load the database if requested.
        if "reload" in args or self.con is None:
            print("Loading database...")
            if self.con is not None:
                self.con.close()
            self.con = connect(":memory:")
            src = connect(self.path_db)
            try:
                src.backup(self.con)
            finally:
                src.close()
        args.pop("reload", None)

        # Prepare the Jinja2 environment.
        env_kwargs = {
            "keep_trailing_newline": True,
            "trim_blocks": True,
            "undefined": jinja2.StrictUndefined,
            "autoescape": False,
        }
        env = jinja2.Environment(**env_kwargs)

        response_code = 200

        if parsed.path == "/logo.svg":
            self.send_response(200)
            self.send_header("Content-type", "image/svg+xml")
            self.end_headers()
            data_svg = importlib.resources.files("stepup.core").joinpath("logo.svg").read_bytes()
            self.wfile.write(data_svg)
            return

        try:
            if parsed.path == "/":
                main = self._main(env)
            elif parsed.path.startswith("/node/"):
                main = self._node(env, args)
            elif parsed.path.startswith("/search/"):
                main = self._search(env, args)
            elif parsed.path.startswith("/search_file/"):
                main = self._search_file(env, args)
            elif parsed.path.startswith("/search_step/"):
                main = self._search_step(env, args)
            else:
                main = self._not_found(env)
                response_code = 404
            main = "\n".join(main)
        except Exception as exc:  # noqa: BLE001
            main = "\n".join(self._error(env, exc))
            response_code = 500

        self.send_response(response_code)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        # Put everything in the HTML template with standard header.
        template = env.from_string(HTML_TEMPLATE)
        template.filename = "<HTML_TEMPLATE>"
        args["reload"] = ["1"]
        reload_url = parsed._replace(query=urlencode(args, doseq=True)).geturl()
        html = template.render(path_db=self.path_db, main=main, reload_url=reload_url)
        self.wfile.write(html.encode("utf-8"))

    # --- main page and subpages ---

    def _main(self, env) -> Iterator[str]:
        # Get a few stats from the database.
        (n_files,) = self.con.execute("SELECT COUNT(*) FROM file").fetchone()
        (n_steps,) = self.con.execute("SELECT COUNT(*) FROM step").fetchone()

        # Get the top-level step plan.py
        label_entry = Step.create_label("./plan.py")
        (i_entry,) = self.con.execute(
            "SELECT i FROM node WHERE kind='step' AND label = ?", (label_entry,)
        ).fetchone()
        a_entry = self._format_node(i_entry, "step", label_entry, False)

        # Format main HTML content.
        template = env.from_string(MAIN_TEMPLATE)
        template.filename = "<MAIN_TEMPLATE>"
        yield template.render(n_files=n_files, n_steps=n_steps, a_entry=a_entry)

    def _node(self, env, args: dict[str, list[str]]) -> Iterator[str]:
        if "i" not in args or len(args["i"]) != 1:
            raise ValueError("Node ID 'i' must be provided exactly once.")
        if not args["i"][0].isdigit():
            raise ValueError("Node ID 'i' must be an integer.")
        node_i = int(args["i"][0])
        (kind, label, creator_i, detached) = self.con.execute(
            "SELECT kind, label, creator, detached FROM node WHERE i = ?", (node_i,)
        ).fetchone()
        node_prefix = "Detached " if detached else ""
        yield f"<h2>{node_prefix}Node {node_i}</h2>"
        yield f"<p><b>Kind:</b> {KIND_NAMES.get(kind, kind)}</p>"
        yield f"<p><b>Label:</b> {label}</p>"

        # Format the state (if a file or a step)
        if kind == "step":
            sql_props = (
                "SELECT state, need, duration, rescheduled_info, subshell, env_overrides,"
                "_safe, _check_safe, _implied_need, _tail_time, _check_after "
                "FROM step WHERE node = ?"
            )
            (
                state_i,
                need_id,
                duration,
                rescheduled_info,
                subshell,
                env_overrides,
                safe,
                check_safe,
                implied_need_id,
                tail_time,
                check_after,
            ) = self.con.execute(sql_props, (node_i,)).fetchone()
            state = StepState(state_i)
            yield f"<p><b>Subshell:</b> {'yes' if subshell else 'no'}</p>"
            yield f'<p><b>State:</b> <span class="{state.name.lower()}">{state.name}</span></p>'
            if rescheduled_info != "":
                yield (
                    '<p><b>Rescheduled:</b> <span class="rescheduled">'
                    f"{rescheduled_info}</span></p>"
                )
            need = Need(need_id)
            implied_need = Need(implied_need_id)
            if need == implied_need:
                yield f"<p><b>Need:</b> {need.name}</p>"
            else:
                yield (
                    f"<p><b>Need:</b> {implied_need.name} (implied by consumers > {need.name})</p>"
                )
            yield f"<p><b>Duration:</b> {duration:.2f} s</p>"
            yield f"<p><b>Tail time:</b> {tail_time:.2f} s</p>"
            if not safe:
                yield (
                    "<p><b>This step is not safe to run:</b> "
                    "a creator does not have state RUNNING or SUCCEEDED.</p>"
                )
            if check_safe:
                yield (
                    "<p><b>The state of this step has not been propagated to the <code>safe</code> "
                    "field of its products yet.</b></p>"
                )
            if check_after:
                yield (
                    "<p><b>The need of this step has not been propagated to the "
                    "<code>_implied_need</code> field of this step and its suppliers yet.</b></p>"
                )

            sql_env = "SELECT name, amended FROM env_var WHERE node = ?"
            env_deps = list(self.con.execute(sql_env, (node_i,)))
            if len(env_deps) > 0:
                yield "<h3>Uses Environment Variables</h3>"
                for env_var, amended in env_deps:
                    line = f"<p>{env_var}"
                    if amended:
                        line += " [amended]"
                    line += "</p>"
                    yield line

            if env_overrides is not None:
                env_overrides = json.loads(env_overrides)
                yield "<h3>Overrides Environment Variables</h3>"
                block = "\n".join(f"{name}={value}" for name, value in env_overrides.items())
                yield f"<pre>{block}</pre>"

            sql_res = "SELECT name, units FROM step_resource WHERE node = ? ORDER BY name"
            resources = list(self.con.execute(sql_res, (node_i,)))
            if len(resources) > 0:
                yield "<h3>Required Resources</h3>"
                for res_name, res_units in resources:
                    yield f"<p><b>{res_name}:</b> {res_units}</p>"

            sql_ngm = "SELECT data FROM nglob_multi WHERE node = ?"
            ngms = list(self.con.execute(sql_ngm, (node_i,)))
            if len(ngms) > 0:
                yield "<h3>Defines NGlob Multis</h3>"
                for row in ngms:
                    ngm = pickle.loads(row[0])
                    yield f"<p>{[ngs.pattern for ngs in ngm.nglob_singles]} {ngm.subs}</p>"

            sql_out = "SELECT kind, content FROM step_output WHERE node = ? ORDER BY kind"
            first = True
            for out_kind, out_content in self.con.execute(sql_out, (node_i,)):
                if first:
                    yield "<h3>Output</h3>"
                    first = False
                yield f"<p>{out_kind.capitalize()}</p>"
                yield f"<pre>{html.escape(out_content)}</pre>"

            sql_sub = (
                "SELECT cmd, workdir, env_overrides, returncode, shell FROM step_subprocess "
                "WHERE node = ? ORDER BY seq"
            )
            subs = list(self.con.execute(sql_sub, (node_i,)))
            if len(subs) > 0:
                yield "<h3>Subprocesses</h3>"
                lines = [
                    format_subprocess(
                        cmd,
                        workdir,
                        None if env_overrides is None else json.loads(env_overrides),
                        returncode,
                        shell=bool(shell_int),
                    )
                    for cmd, workdir, env_overrides, returncode, shell_int in subs
                ]
                yield f"<pre>{html.escape(chr(10).join(lines))}</pre>"

        elif kind == "file":
            (state_i, digest, mode, mtime, size, inode) = self.con.execute(
                "SELECT state, digest, mode, mtime, size, inode FROM file WHERE node = ?",
                (node_i,),
            ).fetchone()
            state = FileState(state_i)
            yield f'<p><b>State:</b> <span class="{state.name.lower()}">{state.name}</span></p>'
            yield f"<p><b>Digest:</b> {fmt_digest(digest)}</p>"
            if len(digest) > 1:
                yield f"<p><b>Mode:</b> {stat.filemode(mode)}</p>"
                yield (
                    "<p><b>Modified:</b> "
                    f"{datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')}</p>"
                )
                yield f"<p><b>Size:</b> {size}</p>"
                yield f"<p><b>Inode:</b> {inode}</p>"

        # Format the creator
        creator = self.con.execute(
            f"SELECT kind, label, detached, {STATE_SQL} FROM node WHERE i = ?", (creator_i,)
        ).fetchone()
        if creator is not None:
            yield "<h3>Creator</h3>"
            yield '<table class="list">'
            creator_kind, creator_label, creator_detached, creator_state_i = creator
            yield self._format_node(
                creator_i, creator_kind, creator_label, creator_detached, creator_state_i
            )
            yield "</table>"

        # Format the products
        yield "<h3>Products</h3>"
        yield '<table class="list">'
        for prod_i, prod_kind, prod_label, state in self.con.execute(
            f"SELECT i, kind, label, {STATE_SQL} FROM node WHERE creator = ? ORDER BY kind, label",
            (node_i,),
        ):
            yield f"{self._format_node(prod_i, prod_kind, prod_label, False, state)}"
        yield "</table>"

        # Format the consumers
        yield "<h3>Consumers</h3>"
        yield '<table class="list">'
        for cons_i, cons_kind, cons_label, amended, state in self.con.execute(
            f"SELECT node.i, kind, label, dependency.i IN amended_dep, {STATE_SQL} FROM node "
            "JOIN dependency ON dependency.consumer = node.i "
            "WHERE dependency.supplier = ? ORDER BY node.kind, node.label",
            (node_i,),
        ):
            yield self._format_node(cons_i, cons_kind, cons_label, False, state, amended)
        yield "</table>"

        # Format the suppliers
        yield "<h3>Suppliers</h3>"
        yield '<table class="list">'
        for sup_i, sup_kind, sup_label, amended, state in self.con.execute(
            f"SELECT node.i, kind, label, dependency.i IN amended_dep, {STATE_SQL} FROM node "
            "JOIN dependency ON dependency.supplier = node.i "
            "WHERE dependency.consumer = ? ORDER BY node.kind, node.label",
            (node_i,),
        ):
            yield self._format_node(sup_i, sup_kind, sup_label, False, state, amended)
        yield "</table>"

    def _search(self, env, args):
        pattern = args.get("pattern", [""])[0]
        yield from self._search_low(env, pattern)

    def _search_file(self, env, args):
        pattern = args.get("pattern", [""])[0]
        yield from self._search_low(env, pattern, "file")

    def _search_step(self, env, args):
        pattern = args.get("pattern", [""])[0]
        yield from self._search_low(env, pattern, "step")

    def _search_low(self, _env, pattern: str, filter_kind: str | None = None) -> Iterator[str]:
        yield "<h2>Search Results</h2>"
        yield f"<p><b>Pattern:</b> {pattern}</p>"
        if filter_kind is None:
            cur = self.con.execute(
                f"SELECT i, kind, label, detached, {STATE_SQL} FROM node "
                "WHERE label GLOB ? ORDER BY kind, label",
                (f"*{pattern}*",),
            )
        else:
            cur = self.con.execute(
                f"SELECT i, kind, label, detached, {STATE_SQL} FROM node "
                "WHERE kind = ? AND label GLOB ? ORDER BY kind, label",
                (filter_kind, f"*{pattern}*"),
            )
        rows = list(cur)
        yield f"<p><b>Number of matches:</b> {len(rows)}</p>"
        yield '<table class="list">'
        for i, kind, label, detached, state in rows:
            yield f"{self._format_node(i, kind, label, detached, state)}"
        yield "</table>"

    def _not_found(self, env, path: str) -> Iterator[str]:
        yield f"<h2>404 Not Found</h2><p>Path {path} is not implemented.</p>"

    def _error(self, env, exc) -> Iterator[str]:
        yield "<h2>500 Internal Server Error</h2>"
        yield "<pre>"
        yield traceback.format_exc()
        yield "</pre>"

    # --- helpers ---

    def _format_node(
        self,
        i: int,
        kind: str,
        label: str,
        detached: bool,
        state: int | None = None,
        amended: bool = False,
    ) -> str:
        sym = KIND_SYMBOLS.get(kind, f"?{kind}?")
        node_str = f"{label}"
        if i is not None:
            node_str = f'<a href="/node/?i={i}">{node_str}</a>'
        if len(label) == 0:
            node_str = f"[{kind}]"
        if detached:
            node_str = f"({node_str})"
        if amended:
            node_str += " <i>[amended]</i>"
        if state is None:
            state_str = ""
        elif kind == "file":
            state_str = FileState(state).name
        elif kind == "step":
            state_str = StepState(state).name
        else:
            state_str = str(state)
        return (
            f'<tr><td class="{state_str.lower()}">{state_str}</td>'
            f"<td>{sym}</td><td>{node_str}</td></tr>"
        )
