"""ipa-analytics MCP server: governed, read-only access to the SaaS analytics warehouse.

Tools
  get_metric_definitions()  -> governed KPI formulas, grains and exclusions from data/metrics.yml
  list_schema()             -> table schemas, column descriptions, row counts and sample values
  run_readonly_sql(query)   -> executes a single SELECT/WITH statement against DuckDB (read-only)

Run directly (stdio transport):  uv run python mcp_server/server.py
Claude Code starts it automatically from .mcp.json.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import yaml
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "saas_analytics.duckdb"
METRICS_PATH = ROOT / "data" / "metrics.yml"
MAX_ROWS = 500

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

mcp = MCPServer(
    "ipa-analytics",
    instructions=(
        "Governed product analytics warehouse. Call get_metric_definitions() before computing any KPI, "
        "list_schema() before querying unfamiliar tables, then run_readonly_sql() with SELECT/WITH only."
    ),
)

# Statements DuckDB could use to mutate state or reach outside the warehouse.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|COPY|EXPORT|IMPORT|ATTACH|DETACH|"
    r"INSTALL|LOAD|PRAGMA|SET|RESET|CALL|CHECKPOINT|VACUUM|GRANT|REVOKE|"
    r"READ_CSV\w*|READ_PARQUET|READ_JSON\w*|READ_TEXT|READ_BLOB|GLOB|SNIFF_CSV)\b",
    re.IGNORECASE,
)


def _connect() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"{DB_PATH.name} not found. Run: uv run python data/seed_data.py")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("SET enable_external_access = false")  # no filesystem / network reads from SQL
    return con


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _without_string_literals(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def validate_readonly_sql(query: str) -> str:
    """Return the cleaned single statement, or raise ValueError explaining why it was rejected."""
    cleaned = _strip_sql_comments(query).strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("Empty query.")
    scan = _without_string_literals(cleaned)
    if ";" in scan:
        raise ValueError("Only a single statement is allowed.")
    if not re.match(r"^(SELECT|WITH)\b", scan, re.IGNORECASE):
        raise ValueError("Query must start with SELECT or WITH.")
    if m := _FORBIDDEN.search(scan):
        raise ValueError(f"Keyword '{m.group(0).upper()}' is not allowed in read-only queries.")
    return cleaned


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


@mcp.tool(annotations=READ_ONLY)
def get_metric_definitions() -> dict:
    """Return the governed semantic layer (data/metrics.yml): KPI SQL expressions, grains, filters,
    exclusions, adoption thresholds and the reporting calendar. Call before writing KPI SQL."""
    return yaml.safe_load(METRICS_PATH.read_text())


@mcp.tool(annotations=READ_ONLY)
def list_schema() -> dict:
    """Return every warehouse table with column names, types, governed descriptions, row counts
    and up to 5 distinct sample values per column."""
    descriptions = yaml.safe_load(METRICS_PATH.read_text()).get("tables", {})
    con = _connect()
    try:
        out: dict[str, Any] = {}
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY 1").fetchall()]
        for t in tables:
            meta = descriptions.get(t, {})
            col_desc = meta.get("columns", {}) if isinstance(meta.get("columns"), dict) else {}
            cols = []
            for name, dtype in con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position", [t]).fetchall():
                samples = [_jsonable(r[0]) for r in con.execute(
                    f'SELECT DISTINCT "{name}" FROM "{t}" WHERE "{name}" IS NOT NULL LIMIT 5').fetchall()]
                if dtype == "VARCHAR":
                    samples = [s[:80] + ("…" if len(s) > 80 else "") for s in samples]
                cols.append({"name": name, "type": dtype, "description": col_desc.get(name, ""), "sample_values": samples})
            out[t] = {
                "description": meta.get("description", ""),
                "row_count": con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0],
                "columns": cols,
            }
        return out
    finally:
        con.close()


@mcp.tool(annotations=READ_ONLY)
def run_readonly_sql(query: str) -> dict:
    """Execute one read-only SELECT/WITH statement against data/saas_analytics.duckdb.

    Returns columns, rows (capped at 500), row_count and the executed SQL so results can be cited.
    Writes, multiple statements, extensions and file/network reads are rejected."""
    try:
        sql = validate_readonly_sql(query)
    except ValueError as e:
        return {"error": str(e), "query": query}
    con = _connect()
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description]
        fetched = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(fetched) > MAX_ROWS
        rows = [[_jsonable(v) for v in r] for r in fetched[:MAX_ROWS]]
        return {"query": sql, "columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
    except duckdb.Error as e:
        return {"error": f"{type(e).__name__}: {e}", "query": sql}
    finally:
        con.close()


if __name__ == "__main__":
    mcp.run()
