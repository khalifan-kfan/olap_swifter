"""
DuckDB viewer for people, not the agent: lists every .duckdb in the workspace,
its tables with row counts, and the first rows of a chosen table.
Read-only, served on localhost only. Standard library + duckdb, nothing else.
"""

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import duckdb

from olap_swifter.paths import route

STYLE = """
:root { --bg:#ffffff; --surface:#f6f8fa; --border:#d0d7de; --text:#1f2328; --muted:#656d76; --accent:#0969da; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --surface:#161b22; --border:#30363d; --text:#e6edf3; --muted:#8b949e; --accent:#58a6ff; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px -apple-system,"Segoe UI",Roboto,sans-serif; display:flex; min-height:100vh; }
nav { width:300px; flex:none; border-right:1px solid var(--border); padding:16px; background:var(--surface); overflow-y:auto; }
main { flex:1; padding:16px; overflow:auto; }
h1 { font-size:16px; margin:0 0 12px; } h2 { font-size:13px; color:var(--muted); margin:16px 0 6px; text-transform:uppercase; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
ul { list-style:none; padding:0; margin:0; } li { padding:3px 0; } li.on a { font-weight:700; }
.n { color:var(--muted); font-size:12px; margin-left:4px; }
table { border-collapse:collapse; font-size:13px; } th,td { border:1px solid var(--border); padding:4px 8px; white-space:nowrap; text-align:left; }
th { background:var(--surface); position:sticky; top:0; } .null { color:var(--muted); font-style:italic; }
.err { color:#cf222e; } .meta { color:var(--muted); margin-bottom:8px; }
"""


def find_databases(extra: Optional[List[Path]] = None) -> List[Path]:
    """Every .duckdb under the workspace, plus any passed explicitly."""
    found = sorted(route("workspace").rglob("*.duckdb")) if route("workspace").exists() else []
    return list(dict.fromkeys([p.resolve() for p in (extra or []) + found]))


def _tables(con) -> List[tuple]:
    rows = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') ORDER BY 1, 2").fetchall()
    out = []
    for schema, name in rows:
        n = con.execute(f'SELECT count(*) FROM "{schema}"."{name}"').fetchone()[0]
        out.append((schema, name, n))
    return out


def render(dbs: List[Path], db_i: Optional[int], table: Optional[str], limit: int) -> str:
    root = route("workspace").parent
    nav = ["<h1>DuckDB viewer</h1><h2>Databases</h2><ul>"]
    for i, p in enumerate(dbs):
        label = p.relative_to(root) if p.is_relative_to(root) else p
        nav.append(f'<li class="{"on" if i == db_i else ""}"><a href="/?{urlencode({"db": i})}">'
                   f"{html.escape(str(label))}</a></li>")
    nav.append("</ul>")
    body = '<p class="meta">Pick a database.</p>' if not dbs or db_i is None else ""
    if not dbs:
        body = '<p class="meta">No .duckdb files found in the workspace.</p>'

    if db_i is not None and 0 <= db_i < len(dbs):
        try:
            con = duckdb.connect(str(dbs[db_i]), read_only=True)
        except Exception as e:  # locked by a running ETL, or not a DuckDB file
            body = f'<p class="err">Cannot open: {html.escape(str(e))}</p>'
        else:
            try:
                tables = _tables(con)
                nav.append("<h2>Tables</h2><ul>")
                for schema, name, n in tables:
                    full = f"{schema}.{name}"
                    nav.append(f'<li class="{"on" if full == table else ""}"><a href="/?'
                               f'{urlencode({"db": db_i, "table": full})}">{html.escape(full)}</a>'
                               f'<span class="n">{n:,}</span></li>')
                nav.append("</ul>")
                match = next(((s, t, n) for s, t, n in tables if f"{s}.{t}" == table), None)
                if match:
                    s, t, n = match
                    cur = con.execute(f'SELECT * FROM "{s}"."{t}" LIMIT {limit}')
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
                    trs = "".join("<tr>" + "".join(
                        '<td class="null">null</td>' if v is None else f"<td>{html.escape(str(v))}</td>"
                        for v in r) + "</tr>" for r in rows)
                    body = (f"<h1>{html.escape(table)}</h1><p class='meta'>{len(rows):,} of {n:,} rows, "
                            f"{len(cols)} columns</p><table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table>")
                elif tables:
                    body = '<p class="meta">Pick a table.</p>'
                else:
                    body = '<p class="meta">This database has no tables.</p>'
            except Exception as e:
                body = f'<p class="err">{html.escape(str(e))}</p>'
            finally:
                con.close()

    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>DuckDB viewer</title><style>{STYLE}</style>"
            f"</head><body><nav>{''.join(nav)}</nav><main>{body}</main></body></html>")


def serve(extra: Optional[List[Path]] = None, port: int = 8765, limit: int = 200) -> None:
    """Serves the viewer on http://127.0.0.1:<port> until interrupted."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlparse(self.path).path != "/":
                self.send_error(404)
                return
            q = parse_qs(urlparse(self.path).query)
            db = q.get("db", [None])[0]
            page = render(find_databases(extra), int(db) if db and db.isdigit() else None,
                          q.get("table", [None])[0], limit)
            data = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"DuckDB viewer on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
