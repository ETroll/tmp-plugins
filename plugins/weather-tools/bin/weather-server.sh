#!/usr/bin/env bash
# Stub MCP stdio server for the weather-tools example plugin.
#
# This is a placeholder to illustrate the Agent Plugins mcp.json "stdio"
# transport (bundled command, ${PLUGIN_ROOT}/${PLUGIN_DATA} expansion, and
# plugin-relative cwd). Replace this with a real MCP server implementation
# (e.g. speaking MCP over stdio in Python or Node) before using this plugin
# for anything real.
set -euo pipefail

echo "weather-server stub: this example does not implement the MCP protocol." >&2
echo "See plugins/weather-tools/README.md for what a real server would need to do." >&2
exit 1
