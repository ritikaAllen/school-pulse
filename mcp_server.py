"""
SchoolPulse — Local MCP server (stdio transport, demo mode)

Serves the synthetic student dataset as MCP tools so any MCP client
(Gemini CLI / Antigravity / ADK) can call get_daily_checkins(),
get_teacher_observations(), and get_student_registry() without a
live Google Sheets connection.

Production path: replace this server entry in mcp_config.json with the
Google Sheets MCP ("google-sheets-mcp") once credentials are available.

Usage:
    python mcp_server.py          # stdio — invoked automatically by mcp_config.json
    mcp dev mcp_server.py         # MCP Inspector (visual debug in browser)

Transport: stdio (local/demo). Production uses SSE over HTTPS.
Design note (whitepaper §MCP): MCP priority for this project is *consumption
over creation* — the Google Sheets MCP entry in mcp_config.json wires to an
official vetted server. This file exists only for the local demo path, per
the spec's "ADK mock MCP tool returning records from JSON files" clause.
"""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA_DIR = Path(os.environ.get("DATA_DIR", "data/synthetic"))


def _load(filename: str) -> list:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


_CHECKINS    = _load("synthetic_checkins.json")
_TEACHER_OBS = _load("teacher_observations.json")
_REGISTRY    = _load("student_registry.json")

mcp = FastMCP("schoolpulse-data")


@mcp.tool()
def get_daily_checkins(date: str) -> list[dict]:
    """
    Return all student check-in records for the given date (YYYY-MM-DD).
    Records include student_id, age_group, emoji_sequence / response text.
    student_name is present — Privacy Guard must be applied before any LLM sees them.
    """
    return [c for c in _CHECKINS if c.get("date") == date]


@mcp.tool()
def get_teacher_observations(date: str) -> list[dict]:
    """
    Return teacher observation records for the given date (YYYY-MM-DD).
    teacher_name is stripped here — it never leaves the MCP layer, per spec.
    """
    records = [o for o in _TEACHER_OBS if o.get("date") == date]
    return [{k: v for k, v in r.items() if k != "teacher_name"} for r in records]


@mcp.tool()
def get_student_registry() -> list[dict]:
    """
    Return the student registry: student_id <-> age_group + fictional_name mapping.
    No contact info or real-world PII is present.
    """
    return _REGISTRY


@mcp.tool()
def list_available_dates() -> list[str]:
    """List all dates present in the dataset (useful for callers to discover valid inputs)."""
    return sorted({c["date"] for c in _CHECKINS if c.get("date")})


if __name__ == "__main__":
    mcp.run()  # stdio transport — invoked by the mcp_config.json "command" entry
