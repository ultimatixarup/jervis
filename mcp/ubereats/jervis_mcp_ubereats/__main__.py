"""Run the Uber Eats MCP server over stdio: `python -m jervis_mcp_ubereats`."""

from __future__ import annotations

from .server import mcp


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
