# plugin-test

Example plugins built to the [Agent Plugins 1.0.0](https://github.com/agentplugins/agent-plugins-spec)
standard — an open, vendor-neutral format for packaging Agent Skills and MCP
servers into distributable plugins.

## Plugins

| Plugin | Demonstrates |
| --- | --- |
| [`plugins/hello-plugin`](plugins/hello-plugin) | Minimal valid plugin: manifest with only `$schema` + `name`, one skill. |
| [`plugins/text-utils`](plugins/text-utils) | Multiple skills in one plugin, a full manifest (version, author, license, keywords), and a bundled `scripts/` file. |
| [`plugins/weather-tools`](plugins/weather-tools) | `mcp.json` with a `stdio` MCP server, `${PLUGIN_ROOT}` / `${PLUGIN_DATA}` placeholder expansion, and a client `extensions` entry with a matching extension directory. |

Each plugin follows the spec's fixed layout:

```text
<plugin-name>/
├── plugin.json     # required manifest ($schema, name, + optional metadata)
├── skills/         # optional — one subdirectory per skill, each with SKILL.md
└── mcp.json        # optional — MCP server configuration
```

`plugin.json` validates against
[`schemas/1.0.0/plugin.schema.json`](https://github.com/agentplugins/agent-plugins-spec/blob/main/schemas/1.0.0/plugin.schema.json),
and `mcp.json` against
[`schemas/1.0.0/mcp.schema.json`](https://github.com/agentplugins/agent-plugins-spec/blob/main/schemas/1.0.0/mcp.schema.json).
Skills follow the [Agent Skills specification](https://agentskills.io/specification)
for `SKILL.md` frontmatter and layout.

Note: `weather-tools`' bundled MCP server (`bin/weather-server.sh`) is a stub
that does not implement the MCP protocol — it exists only to show the
`mcp.json` configuration shape, not to be run for real.
