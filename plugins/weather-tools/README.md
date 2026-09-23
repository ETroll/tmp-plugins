# weather-tools (example)

Demonstrates the two most involved parts of Agent Plugins 1.0.0 in one plugin:

- `mcp.json` with a `stdio` MCP server, using a plugin-relative `command`
  (`./bin/weather-server.sh`) and `${PLUGIN_ROOT}` / `${PLUGIN_DATA}`
  placeholder expansion in `args`, `env`, and `cwd`.
- `extensions` in `plugin.json` plus a matching `com.example.client/`
  extension directory, per spec section 8.

`bin/weather-server.sh` is a stub — it does not implement the MCP protocol.
Swap it for a real MCP server (any language, speaking MCP over stdio) to make
this plugin functional.
