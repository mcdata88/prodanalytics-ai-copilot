"""Smoke tests: validator unit tests + an end-to-end MCP stdio round trip."""

import json
import sys
from pathlib import Path

import anyio
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_server"))

from server import validate_readonly_sql  # noqa: E402


@pytest.mark.parametrize("q", [
    "SELECT 1",
    "with t as (select 1 as x) select * from t",
    "-- comment\nSELECT 'a;b' AS x;",
    "SELECT replace(account_name, 'Inc', '') FROM accounts",
])
def test_allows_reads(q):
    validate_readonly_sql(q)


@pytest.mark.parametrize("q", [
    "DROP TABLE accounts",
    "SELECT 1; DELETE FROM accounts",
    "WITH x AS (SELECT 1) INSERT INTO accounts SELECT * FROM x",
    "SELECT * FROM read_csv('/etc/passwd')",
    "/* sneaky */ ATTACH 'other.db'",
    "",
])
def test_rejects_writes_and_escapes(q):
    with pytest.raises(ValueError):
        validate_readonly_sql(q)


def test_stdio_round_trip():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py")])
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert names == {"get_metric_definitions", "list_schema", "run_readonly_sql"}
            res = await s.call_tool("run_readonly_sql", {"query": "SELECT COUNT(*) AS n FROM accounts"})
            payload = json.loads(res.content[0].text)
            assert payload["rows"] == [[500]]
            defs = json.loads((await s.call_tool("get_metric_definitions", {})).content[0].text)
            assert "net_dollar_retention" in defs["metrics"]

    anyio.run(run)
