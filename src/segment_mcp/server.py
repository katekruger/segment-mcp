"""Entry point for the segment-mcp server.

Tool registration lands starting Prompt 2, once client/public_api.py and
modes.py exist. See BUILD-PLAN.md §8 for the intended module layout.
"""

# mcp>=2 renamed FastMCP to MCPServer — see
# https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("segment_mcp")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
